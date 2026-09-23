'use client'

import { useEffect, useRef, useState } from 'react'
import { authStore } from '@/features/auth/store'
import { reissue } from '@/features/auth/api'
import { API_BASE_URL } from '@/lib/fetcher'
import { JOBS_PATH, jobRealtimeEventSchema } from './schema'
import type { JobRealtimeEvent } from './types'

// GET /jobs/{jobId}/events (SSE) 구독.
//
// ⚠️ 브라우저 기본 `EventSource` 를 쓸 수 없다. 이 엔드포인트는 Authorization 헤더로만 인증하는데
//    EventSource 는 헤더를 붙이지 못한다. 토큰을 쿼리로 넘기면 nginx 액세스 로그에 남아서 피한다.
//    → `fetch` 로 헤더를 실어 요청하고 `response.body` 스트림을 직접 파싱한다.
//
// 역할 분담(스펙 §7): **이벤트 = 즉시 UI 갱신 / REST = 진실의 근거**.
// 그래서 이 훅은 "화면을 직접 고치지" 않고 이벤트를 올려보내기만 하고,
// 연결이 끊겼다 붙을 때마다 `onResync` 로 호출자에게 REST 재조회를 시킨다.

// 재시도는 지수 백오프 — 서버가 죽어 있거나 토큰 재발급까지 실패하면 3초마다 두드려 봐야 소용없다.
// 연결에 성공하면 처음 값으로 되돌린다
const RETRY_MIN_MS = 3_000
const RETRY_MAX_MS = 30_000

export type JobRealtimeState = {
  /** 마지막으로 받은 이벤트 — 없으면 아직 아무 소식 없음 */
  event: JobRealtimeEvent | null
  /** 스트림이 붙어 있나. false 면 호출자는 기존 폴링으로 버틴다 */
  connected: boolean
}

/** SSE 프레임(빈 줄로 구분) 중 `data:` 줄만 모아 JSON 으로. 주석(`:`)·`event:` 줄은 버린다 —
 *  이벤트 종류는 payload 안 `type` 에도 있어서 그걸 쓴다(둘이 같은 값). */
function parseFrame(frame: string): unknown | null {
  const data = frame
    .split('\n')
    .filter((l) => l.startsWith('data:'))
    .map((l) => l.slice(5).trim())
    .join('\n')
  if (!data) return null
  try {
    return JSON.parse(data)
  } catch {
    return null // 깨진 프레임 하나가 스트림 전체를 끊지 않게
  }
}

/** 늦게 도착한 옛 이벤트로 진행률이 되돌아가지 않게 한다(스펙 §7 out-of-order).
 *  종료 이벤트는 언제나 최신이다 — 끝난 작업이 다시 진행 중이 될 수는 없다. */
function isStale(prev: JobRealtimeEvent | null, next: JobRealtimeEvent): boolean {
  if (!prev) return false
  if (next.type !== 'JOB_PROGRESS') return false
  if (prev.type !== 'JOB_PROGRESS') return true // 이미 종료된 뒤 온 진행률은 버린다
  // ⚠️ 문자열 비교 금지 — 백엔드 Instant 는 소수부 자릿수가 가변이라("…03Z" vs "…03.400Z")
  //    사전순으로는 'Z' > '.' 이 돼 늦은 이벤트를 옛것으로 오판한다. 시각으로 파싱해 비교한다
  const a = prev.updatedAt ? Date.parse(prev.updatedAt) : NaN
  const b = next.updatedAt ? Date.parse(next.updatedAt) : NaN
  if (!Number.isNaN(a) && !Number.isNaN(b) && a !== b) return b < a
  return (next.progress ?? 0) < (prev.progress ?? 0)
}

export function useJobRealtime(jobId: number, enabled: boolean, onResync?: () => void): JobRealtimeState {
  const [state, setState] = useState<JobRealtimeState>({ event: null, connected: false })
  // 콜백을 ref 로 — 호출자가 매 렌더 새 함수를 넘겨도 구독을 다시 맺지 않는다
  const resyncRef = useRef(onResync)
  resyncRef.current = onResync

  useEffect(() => {
    if (!enabled) return
    const ac = new AbortController()
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let wake: (() => void) | undefined // 대기 중인 재시도 — cleanup 에서 깨워 루프를 즉시 끝낸다
    let backoff = RETRY_MIN_MS
    // 다른 작업으로 옮겨 붙을 때 이전 작업의 이벤트가 남아 있으면 안 된다.
    // 특히 이전 작업의 종료 이벤트가 남으면 isStale 이 새 작업의 진행률을 전부 버린다
    setState({ event: null, connected: false })

    const run = async () => {
      while (!stopped) {
        try {
          const token = authStore.get().accessToken ?? (await reissue())
          if (!token) return // 로그인 상태가 아니면 조용히 포기 — 폴링이 화면을 지킨다
          const res = await fetch(`${API_BASE_URL}${JOBS_PATH}/${jobId}/events`, {
            headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
            signal: ac.signal,
          })
          // 404·501 = 이 서버엔 이 엔드포인트가 없다(구버전·목업). 일시 장애가 아니므로 재시도하지 않는다 —
          // 계속 두드리면 3초마다 404 를 만드는 루프가 된다. 폴링이 화면을 맡는다
          if (res.status === 404 || res.status === 501) return
          // 토큰이 만료된 채로 재시도하면 3초마다 401 만 찍는다 — 한 번 갈아 끼우고 다음 회차에 다시 붙는다
          if (res.status === 401) {
            await reissue()
            throw new Error('SSE 401')
          }
          if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`)

          // 재연결이면 그사이 놓친 변화가 있다 → REST 로 현재 상태를 맞추고 시작(스펙 §7 reconnect)
          resyncRef.current?.()
          backoff = RETRY_MIN_MS // 붙었으니 다음 끊김은 다시 3초부터
          if (!stopped) setState((s) => ({ ...s, connected: true }))

          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
          let buffer = ''
          for (;;) {
            const { done, value } = await reader.read()
            if (done || stopped) break
            buffer += value
            // 프레임 구분은 빈 줄(\r\n 도 받아 준다). 남는 꼬리는 다음 청크와 이어 붙인다
            for (;;) {
              const m = /\r?\n\r?\n/.exec(buffer)
              if (!m) break
              const frame = buffer.slice(0, m.index)
              buffer = buffer.slice(m.index + m[0].length)
              const parsed = jobRealtimeEventSchema.safeParse(parseFrame(frame))
              if (!parsed.success) continue // 모르는 형태의 프레임은 조용히 버린다(스트림은 유지)
              const ev = parsed.data
              // 다른 작업의 이벤트가 섞여 오면 버린다 — 구독은 작업 단위지만 방어해 둔다
              if (ev.jobId === jobId) setState((s) => (isStale(s.event, ev) ? s : { event: ev, connected: true }))
            }
          }
        } catch {
          /* 네트워크·인증·중단 — 아래 재시도로 흘린다 */
        }
        if (stopped) return
        setState((s) => ({ ...s, connected: false }))
        await new Promise<void>((r) => {
          wake = r // cleanup 이 이 promise 를 깨워야 루프가 매달린 채 남지 않는다
          timer = setTimeout(r, backoff)
        })
        backoff = Math.min(backoff * 2, RETRY_MAX_MS)
      }
    }
    void run()

    return () => {
      stopped = true
      ac.abort()
      if (timer) clearTimeout(timer)
      wake?.() // 대기 중이면 깨워서 즉시 종료(깨어난 직후 stopped 검사에 걸린다)
    }
  }, [jobId, enabled])

  return state
}
