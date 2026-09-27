import type { DatasetMetadata } from './types'

// 데이터셋 파일을 브라우저에서 읽어 AI DatasetMetadata 5값을 뽑는다(job.md §4-3). 파일은 서버로 가지 않는다.
// 토큰 수는 토크나이저 없이 문자 종류별 비율로 근사 — 견적용이며, 실행 시 서버가 정확히 다시 센다.
// ponytail: 문자→토큰 비율은 경험값(한글 1.5자·CJK 1자·그 외 4자 ≈ 1토큰). 모델별 토크나이저를 쓰게 되면 estimateTokens 만 교체
const HANGUL = /\p{Script=Hangul}/u
const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u
const MAX_DEPTH = 4

export function estimateTokens(text: string): number {
  let hangul = 0
  let cjk = 0
  let other = 0
  for (const ch of text) {
    if (HANGUL.test(ch)) hangul++
    else if (CJK.test(ch)) cjk++
    else if (!/\s/.test(ch)) other++
  }
  return Math.ceil(hangul / 1.5 + cjk + other / 4)
}

/** JSON 값 안의 문자열을 전부 이어 붙인다(중첩 4단계까지). 라벨·숫자도 문자열이면 포함 — 모델이 보는 텍스트에 가깝게 */
export function textOfJson(value: unknown, depth = 0): string {
  if (typeof value === 'string') return value
  if (depth >= MAX_DEPTH || value === null || typeof value !== 'object') return typeof value === 'number' || typeof value === 'boolean' ? String(value) : ''
  return Object.values(value as Record<string, unknown>)
    .map((v) => textOfJson(v, depth + 1))
    .filter(Boolean)
    .join(' ')
}

/** 샘플 한 줄의 텍스트 — JSON 이면 문자열 값들, 아니면 그대로 */
export function sampleText(line: string): string {
  const t = line.trim()
  if (t.startsWith('{') || t.startsWith('[')) {
    try {
      return textOfJson(JSON.parse(t))
    } catch {
      /* JSON 이 아니면 원문 */
    }
  }
  return t
}

/** 파일 SHA-256 → "0x" + 64자(back datasets.data_hash CHAR(66)). POST /datasets 의 dataHash */
// 파일을 통째로 메모리에 올린다 — SubtleCrypto 에 스트리밍 digest 가 없어서, 이걸 피하려면 sha-256 을
// 직접 구현해야 한다. 해시가 한 비트라도 틀리면 서버가 업로드를 통째로 거절하므로 손으로 쓴 구현을
// 검증 없이 들이지 않는다(수정요청서 FE-11). 대신 **터질 때 왜 터졌는지는 말해 준다** —
// 예전에는 브라우저의 RangeError 가 그대로 올라가 "잠시 후 다시 시도" 로 뭉개졌고, 몇 번을 다시 해도 같았다.
const TOO_BIG_FOR_HASH = 'FILE_TOO_LARGE_TO_HASH'
export async function hashFile(file: File): Promise<string> {
  let buf: ArrayBuffer
  try {
    buf = await file.arrayBuffer()
  } catch {
    // 브라우저가 이 크기의 버퍼를 못 잡았다(RangeError·OOM). 파일을 나눠 올리는 것 말고는 방법이 없다
    throw new Error(TOO_BIG_FOR_HASH)
  }
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return '0x' + Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 위 한계에 걸린 오류인가 — 화면이 "잠시 후 다시" 대신 파일을 줄이라고 말하게 한다 */
export const isTooBigToHash = (e: unknown) => e instanceof Error && e.message === TOO_BIG_FOR_HASH

/** 샘플 텍스트 목록 → 5값 */
export function summarize(sampleTokens: number[], sizeBytes: number): DatasetMetadata {
  const n = sampleTokens.length
  if (n === 0) throw new Error('EMPTY_DATASET')
  const total = sampleTokens.reduce((a, b) => a + b, 0)
  if (total === 0) throw new Error('EMPTY_DATASET')
  return { sizeBytes, sampleCount: n, totalTokens: total, avgSeqLength: total / n, maxSeqLength: Math.max(...sampleTokens) }
}

/**
 * 파일 → 메타데이터. JSONL/CSV/TXT 는 줄 단위 스트리밍(큰 파일도 메모리에 다 올리지 않음), JSON 배열은 통째로 파싱.
 * CSV 첫 줄은 헤더로 보고 건너뛴다.
 */
export async function analyzeDataset(file: File, onProgress?: (bytesRead: number) => void): Promise<DatasetMetadata> {
  const name = file.name.toLowerCase()
  if (name.endsWith('.json')) {
    const parsed = JSON.parse(await file.text())
    const items: unknown[] = Array.isArray(parsed) ? parsed : Array.isArray((parsed as { data?: unknown[] })?.data) ? (parsed as { data: unknown[] }).data : [parsed]
    return summarize(items.map((it) => estimateTokens(textOfJson(it))), file.size)
  }

  const tokens: number[] = []
  let skipHeader = name.endsWith('.csv')
  let rest = ''
  let read = 0
  const reader = file.stream().pipeThrough(new TextDecoderStream()).getReader()
  const take = (line: string) => {
    if (skipHeader) {
      skipHeader = false
      return
    }
    if (!line.trim()) return
    tokens.push(estimateTokens(sampleText(line)))
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    read += value.length
    onProgress?.(read)
    const parts = (rest + value).split('\n')
    rest = parts.pop() ?? ''
    parts.forEach(take)
  }
  if (rest) take(rest)
  return summarize(tokens, file.size)
}
