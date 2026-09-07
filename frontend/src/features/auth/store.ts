import { useSyncExternalStore } from 'react'
import type { AuthState } from './types'

// 액세스 토큰은 메모리에만 (localStorage 금지 — XSS 노출). 새로고침 시엔 refresh 쿠키로 재발급해 복구한다(authProvider).
// 도메인 스토어라 features/auth 안에 둔다. zustand 없이 useSyncExternalStore 로 충분.
let state: AuthState = { status: 'loading', accessToken: null }
const SERVER_SNAPSHOT: AuthState = { status: 'loading', accessToken: null }
const listeners = new Set<() => void>()

export const authStore = {
  get: () => state,
  set(patch: Partial<AuthState>) {
    state = { ...state, ...patch }
    listeners.forEach((l) => l())
  },
  signIn(accessToken: string) {
    authStore.set({ status: 'authed', accessToken })
  },
  signOut() {
    authStore.set({ status: 'guest', accessToken: null })
  },
  subscribe(l: () => void) {
    listeners.add(l)
    return () => listeners.delete(l)
  },
}

export function useAuth(): AuthState {
  return useSyncExternalStore(authStore.subscribe, authStore.get, () => SERVER_SNAPSHOT)
}
