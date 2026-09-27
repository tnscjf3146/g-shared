// 입력 LoRA 어댑터 파일 검사 — 브라우저에서 앞부분만 읽어 tar 헤더를 훑는다(lora-adapter-frontend-작업 §1).
// .bin/.pt(pickle)은 임의 코드 실행 위험이라 받지 않고, 압축본(.tar.gz·.zip)도 Worker 가 못 푼다 — 비압축 .tar 하나만.
// tar 는 512바이트 헤더 + 내용(512 배수 패딩)의 반복이라 헤더 블록만 건너뛰며 읽으면 목록을 알 수 있다. 전체를 메모리에 올리지 않는다.

export const ADAPTER_ACCEPT = '.tar'
export const ADAPTER_MAX_BYTES = 1024 ** 3 // 1 GiB (Backend·Worker 와 같은 상한)
/** 루트에 반드시 있어야 하는 항목 */
const REQUIRED = ['adapter_config.json', 'adapter_model.safetensors']
/** 그 밖에 허용하는 항목 — 완료 작업의 result.tar 를 그대로 다시 넣을 수 있게 */
const ALLOWED = new Set([...REQUIRED, 'training_metrics.json'])
const BLOCK = 512
/** 헤더가 이보다 많으면 어댑터가 아니다 — 깨진 파일에서 끝없이 읽지 않게(Backend MAX_ENTRIES 와 같은 값) */
const MAX_ENTRIES = 64
const HOWTO = '`tar -cf adapter.tar adapter_config.json adapter_model.safetensors` 로 만들어 주세요.'

/** tar 헤더의 문자열 필드는 첫 NUL 까지 — 그 뒤 패딩에 무엇이 있든 보지 않는다 */
const text = (b: Uint8Array) => {
  const nul = b.indexOf(0)
  return new TextDecoder().decode(nul < 0 ? b : b.subarray(0, nul)).trim()
}

/** tar 헤더 하나 → 이름·크기·일반 파일 여부. 이름이 비면 아카이브 끝(널 블록) */
function header(h: Uint8Array): { name: string; size: number; regular: boolean } | null {
  const name = text(h.subarray(0, 100))
  if (!name) return null
  const size = parseInt(text(h.subarray(124, 136)) || '0', 8)
  const flag = h[156]
  return { name, size: Number.isFinite(size) && size >= 0 ? size : -1, regular: flag === 0 || flag === 0x30 /* '0' */ }
}

/**
 * 어댑터 파일 검사 — 통과하면 null, 아니면 사용자에게 보일 오류 문구.
 * 이름은 접두사 없이 정확히 일치해야 한다(`./adapter_config.json` 도 거절) — Backend·Worker 와 같은 규칙.
 */
export async function checkAdapterTar(file: File): Promise<string | null> {
  if (!file.name.toLowerCase().endsWith('.tar')) return `비압축 .tar 파일만 올릴 수 있습니다(.zip·.tar.gz·.safetensors 단독 불가). ${HOWTO}`
  if (file.size === 0) return '빈 파일입니다. 어댑터 .tar 파일을 다시 확인해 주세요.'
  if (file.size > ADAPTER_MAX_BYTES) return '어댑터는 1 GiB 이하여야 합니다.'

  const found = new Set<string>()
  let seen = 0
  for (let at = 0; at + BLOCK <= file.size; ) {
    if (++seen > MAX_ENTRIES) return `어댑터 .tar 에 항목이 너무 많습니다. ${HOWTO}`
    const h = new Uint8Array(await file.slice(at, at + BLOCK).arrayBuffer())
    const entry = header(h)
    if (!entry) break // 널 블록 = 끝
    if (entry.size < 0) return `어댑터 .tar 를 읽지 못했습니다(헤더가 깨졌습니다). ${HOWTO}`
    if (!entry.regular) return `어댑터 .tar 에는 일반 파일만 담을 수 있습니다(디렉터리·링크 불가). ${HOWTO}`
    if (!ALLOWED.has(entry.name)) return `어댑터 .tar 에 쓸 수 없는 항목이 있습니다: ${entry.name}. ${HOWTO}`
    found.add(entry.name)
    at += BLOCK + Math.ceil(entry.size / BLOCK) * BLOCK
  }
  const missing = REQUIRED.filter((n) => !found.has(n))
  return missing.length ? `어댑터 .tar 에 ${missing.join(' · ')} 가 없습니다. ${HOWTO}` : null
}
