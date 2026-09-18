// 완료된 작업을 "확인했는지" 기억한다 — 확인 전엔 초록(눈에 띔), 상세를 보면 회색(가라앉음).
// 서버에 없는 순수 클라이언트 표시 상태라 localStorage 에만 둔다. 다른 탭·같은 탭 갱신은 storage 이벤트로 알린다.
const KEY = 'gshared.jobs.viewedCompleted'

const read = (): Set<number> => {
  try {
    const raw = localStorage.getItem(KEY)
    return new Set<number>(raw ? (JSON.parse(raw) as number[]) : [])
  } catch {
    return new Set<number>()
  }
}

export const isCompletedViewed = (jobId: number): boolean => read().has(jobId)

export const markCompletedViewed = (jobId: number): void => {
  try {
    const s = read()
    if (s.has(jobId)) return
    s.add(jobId)
    localStorage.setItem(KEY, JSON.stringify([...s]))
    window.dispatchEvent(new Event('storage')) // 같은 탭은 storage 이벤트가 안 나므로 직접 알림
  } catch {
    /* 저장소 불가 환경이면 표시만 안 될 뿐 기능은 그대로 */
  }
}

/** useSyncExternalStore 구독기 — 다른 곳에서 markCompletedViewed 하면 목록 색이 즉시 바뀐다 */
export const subscribeViewed = (cb: () => void): (() => void) => {
  window.addEventListener('storage', cb)
  return () => window.removeEventListener('storage', cb)
}
