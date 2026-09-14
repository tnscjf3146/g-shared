// 현재 챕터 인덱스(0 = 카드, 1.. = CHAPTERS) 를 landingScene 이 쓰고 chapterSidebar·chapterCopy 가 읽는 아주 작은 외부 스토어.
// React state 를 거치지 않는 이유: landingScene 은 스크롤마다 갱신하고 사이드바·카피만 리렌더되면 되기 때문(useSyncExternalStore)
type Listener = () => void
let index = -1 // -1 = 인트로 중(사이드바·카피 숨김)
let jumper: ((i: number) => void) | null = null
const listeners = new Set<Listener>()

export const chapterStore = {
  subscribe(cb: Listener) {
    listeners.add(cb)
    return () => {
      listeners.delete(cb)
    }
  },
  get: () => index,
  set(i: number) {
    if (i === index) return
    index = i
    listeners.forEach((l) => l())
  },
  /** landingScene 이 등록 — 사이드바 클릭 시 그 챕터 위치로 스크롤 */
  setJumper(fn: ((i: number) => void) | null) {
    jumper = fn
  },
  jump(i: number) {
    jumper?.(i)
  },
}
