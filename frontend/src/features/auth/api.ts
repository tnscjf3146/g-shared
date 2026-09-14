import { ApiError, fetcher } from '@/lib/fetcher'
import { RETRYABLE_401 } from './errors'
import { USERS_ME_PATH, meSchema, tokenResponseSchema } from './schema'
import { authStore } from './store'
import { safeNextPath } from './next-path'
import type { LoginInput, Me, SignupRequest } from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

// refresh 쿠키는 HttpOnly 라 JS 가 존재를 알 수 없다 → 로그인 성공 시 "세션 있음" 표시만 남겨 두고(토큰은 저장하지 않음),
// 표시가 있을 때만 reissue 를 호출한다. 그래야 로그인 전 첫 진입마다 콘솔에 401 이 찍히지 않는다
export const SESSION_FLAG = 'gshared.session' // 다른 탭의 로그인/로그아웃을 storage 이벤트로 감지하는 키(authProvider)
const sessionFlag = {
  has: () => {
    try {
      return localStorage.getItem(SESSION_FLAG) === '1'
    } catch {
      return false
    }
  },
  set: (on: boolean) => {
    try {
      if (on) localStorage.setItem(SESSION_FLAG, '1')
      else localStorage.removeItem(SESSION_FLAG)
    } catch {
      /* 저장소 불가 환경이면 표시 없이 진행 */
    }
  },
}

/** POST /auth/signup → 201(빈 본문). 중복 이메일은 409 DUPLICATE_EMAIL */
export function signup(body: SignupRequest): Promise<void> {
  return fetcher<void>('/auth/signup', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
}

/** POST /auth/login → accessToken (refresh 는 HttpOnly 쿠키로 브라우저가 받음) */
export async function login(input: LoginInput): Promise<string> {
  const res = await fetcher<unknown>('/auth/login', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(input) })
  const { accessToken } = tokenResponseSchema.parse(res)
  sessionFlag.set(true)
  authStore.signIn(accessToken)
  return accessToken
}

// 재발급은 동시에 하나만 — 요청 여러 개가 동시에 401 나도 reissue 는 1번
let inflight: Promise<string | null> | null = null
/** POST /auth/reissue(쿠키) → 새 accessToken. 로그인 이력(세션 표시)이 없으면 호출 없이 guest. 실패(쿠키 없음/만료)면 표시 지우고 guest */
export function reissue(): Promise<string | null> {
  if (!sessionFlag.has()) {
    authStore.signOut()
    return Promise.resolve(null)
  }
  inflight ??= fetcher<unknown>('/auth/reissue', { method: 'POST' })
    .then((res) => {
      const { accessToken } = tokenResponseSchema.parse(res)
      authStore.signIn(accessToken)
      return accessToken
    })
    .catch((e) => {
      // 401(쿠키 없음·만료·무효)만 진짜 세션 종료. 네트워크 오류·429·5xx 는 서버 토큰이 살아 있으니 표시를 남겨 두고
      // 이번엔 guest 로만 둔다 → 다음 로드/authFetch 에서 다시 시도해 복구된다(자동 재시도는 429 를 악화시키므로 안 함)
      if (e instanceof ApiError && e.status === 401) sessionFlag.set(false)
      authStore.signOut()
      return null
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** POST /auth/logout(Bearer). 성공/실패와 무관하게 로컬 세션은 비운다 */
export async function logout(): Promise<void> {
  const token = authStore.get().accessToken
  try {
    if (token) await fetcher<void>('/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
  } finally {
    sessionFlag.set(false)
    authStore.signOut()
  }
}

/** 보호 API 호출: Bearer 첨부 → 토큰 관련 401 이면 reissue 1회 후 재시도 → 그래도 실패면 로그인으로 */
export async function authFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const call = (token: string) =>
    fetcher<T>(path, { ...init, headers: { ...(init.headers as Record<string, string> | undefined), Authorization: `Bearer ${token}` } })
  const token = authStore.get().accessToken ?? (await reissue())
  if (!token) return reissueFailed()
  try {
    return await call(token)
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401 && RETRYABLE_401.has(e.code))) throw e
    const fresh = await reissue()
    if (!fresh) return reissueFailed()
    return call(fresh)
  }
}

// reissue 가 null 인데 세션 표시가 남아 있으면 401 이 아닌 일시 장애(네트워크·429·5xx) → 로그인으로 보내지 않고 호출자에게 실패만 알린다
function reissueFailed(): never {
  if (sessionFlag.has()) throw new ApiError(503, 'SERVICE_UNAVAILABLE', '잠시 후 다시 시도해 주세요.')
  return toLogin()
}

function toLogin(): never {
  sessionFlag.set(false)
  authStore.signOut()
  if (typeof window !== 'undefined') {
    const next = encodeURIComponent(safeNextPath(location.pathname + location.search)) // 소비 측(loginForm)도 다시 검증하지만 생성 시점에도 정규화
    location.assign(new URL(`/login?next=${next}&reason=expired`, location.origin).href) // React 밖(API 계층)이라 라우터를 못 쓴다 → 전체 URL 로 하드 이동
  }
  throw new ApiError(401, 'UNAUTHORIZED', '인증이 필요합니다.')
}

/** GET /users/me — 내 프로필. 백엔드 미구현: 계약만 확정(schema.ts meSchema), MSW 목업이 응답 */
export async function getMe(): Promise<Me> {
  return meSchema.parse(await authFetch<unknown>(USERS_ME_PATH))
}

/** PATCH /users/me { nickname } → 204. 백엔드 미구현 — 목업 응답 */
export function updateNickname(nickname: string): Promise<void> {
  return authFetch<void>(USERS_ME_PATH, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ nickname }) })
}
