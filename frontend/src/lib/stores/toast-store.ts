import { useSyncExternalStore } from 'react'

// 전역 토스트 — 화면 어디서 띄우든 한 곳에 쌓이고 한 곳에서 그린다.
// auth/store.ts 와 같은 방식(useSyncExternalStore, zustand 없음). AGENTS.md 폴더 구조가
// lib/stores/toast-store.ts 를 지정하고 있고, "UI·토스트 수준의 소수만" 전역 허용이라는 규칙에 해당한다.

export type ToastTone = 'success' | 'danger' | 'neutral'
export type Toast = {
  id: number
  tone: ToastTone
  message: string
  /** 있으면 토스트 안에 링크 버튼으로 (예: "결과 보기") */
  action?: { label: string; href: string }
  /** 같은 사건을 두 번 띄우지 않기 위한 키. 재연결·재조회로 같은 상태를 다시 관측해도 1회만 */
  dedupeKey?: string
}

const AUTO_DISMISS_MS = 6_000

let seq = 0
let toasts: Toast[] = []
const shown = new Set<string>() // dedupeKey 기록 — 세션 동안 유지
const SERVER_SNAPSHOT: Toast[] = []
const listeners = new Set<() => void>()

const emit = () => listeners.forEach((l) => l())

export const toastStore = {
  get: () => toasts,
  subscribe(l: () => void) {
    listeners.add(l)
    return () => listeners.delete(l)
  },
  /** 띄운다. dedupeKey 가 이미 쓰였으면 무시하고 false 를 돌려준다 */
  push(t: Omit<Toast, 'id'>): boolean {
    if (t.dedupeKey) {
      if (shown.has(t.dedupeKey)) return false
      shown.add(t.dedupeKey)
    }
    const toast: Toast = { ...t, id: ++seq }
    toasts = [...toasts, toast]
    emit()
    // 자동 소멸 — 사용자가 먼저 닫으면 dismiss 가 이미 지웠고, 여기선 없는 id 라 무해
    setTimeout(() => toastStore.dismiss(toast.id), AUTO_DISMISS_MS)
    return true
  },
  dismiss(id: number) {
    const next = toasts.filter((t) => t.id !== id)
    if (next.length === toasts.length) return // 이미 없으면 리렌더 유발하지 않는다
    toasts = next
    emit()
  },
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(toastStore.subscribe, toastStore.get, () => SERVER_SNAPSHOT)
}
