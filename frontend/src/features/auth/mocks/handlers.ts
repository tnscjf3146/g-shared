import { delay, http, HttpResponse } from 'msw'
import { API_BASE_URL } from '@/lib/fetcher'
import { USERS_ME_PATH, emailSchema, nicknameSchema, passwordSchema } from '../schema'
import { MOCK_ACCESS_TOKEN, MOCK_REFRESH_COOKIE, MOCK_USER } from './data'

// 내 계정 목업 상태 — 닉네임 변경이 화면에 반영되게 모듈 변수(새로고침하면 초기값)
let nickname = MOCK_USER.nickname

// 백엔드 에러 포맷 그대로: { code, message }
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
// HttpOnly 를 붙이지 않는다(실서버와 다른 유일한 점): MSW 는 HttpOnly 쿠키를 자기 저장소에만 두고 document.cookie 에 못 쓰므로
// 브라우저의 실제 페이지 이동에 실리지 않아 proxy.ts 보호 경로(/wallet 등)가 목업 모드에서 항상 /login 으로 튕긴다
const tokenJson = (setCookie: boolean) =>
  HttpResponse.json(
    { accessToken: MOCK_ACCESS_TOKEN },
    { headers: setCookie ? { 'Set-Cookie': `${MOCK_REFRESH_COOKIE}=mock-refresh; Path=/; SameSite=Lax` } : undefined },
  )

export const authHandlers = [
  // 내 계정 — back UserController 와 같은 응답(로컬 목업용)
  http.get(`${API_BASE_URL}${USERS_ME_PATH}`, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')?.startsWith('Bearer ')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    return HttpResponse.json({ email: MOCK_USER.email, nickname })
  }),
  http.patch(`${API_BASE_URL}${USERS_ME_PATH}`, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')?.startsWith('Bearer ')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const body = (await request.json().catch(() => null)) as { nickname?: string } | null
    const r = nicknameSchema.safeParse(body?.nickname ?? '')
    if (!r.success) return error(400, 'INVALID_INPUT', r.error.issues[0].message)
    nickname = r.data
    return new HttpResponse(null, { status: 204 })
  }),

  http.post(`${API_BASE_URL}/auth/signup`, async ({ request }) => {
    await delay()
    const body = (await request.json().catch(() => null)) as { email?: string; password?: string; nickname?: string } | null
    // 서버(@Valid)처럼 첫 필드 오류 메시지 하나만 INVALID_INPUT 으로
    for (const [schema, value] of [[emailSchema, body?.email], [passwordSchema, body?.password], [nicknameSchema, body?.nickname]] as const) {
      const r = schema.safeParse(value ?? '')
      if (!r.success) return error(400, 'INVALID_INPUT', r.error.issues[0].message)
    }
    if (body?.email === MOCK_USER.email) return error(409, 'DUPLICATE_EMAIL', '이미 사용중인 이메일입니다.')
    return new HttpResponse(null, { status: 201 })
  }),

  http.post(`${API_BASE_URL}/auth/login`, async ({ request }) => {
    await delay()
    const body = (await request.json().catch(() => null)) as { email?: string; password?: string } | null
    if (body?.email === MOCK_USER.email && body?.password === MOCK_USER.password) return tokenJson(true)
    return error(401, 'INVALID_CREDENTIALS', '아이디 또는 비밀번호가 올바르지 않습니다.')
  }),

  http.post(`${API_BASE_URL}/auth/reissue`, async ({ cookies }) => {
    await delay()
    if (!cookies[MOCK_REFRESH_COOKIE]) return error(401, 'INVALID_TOKEN', '토큰이 유효하지 않습니다.')
    return tokenJson(true)
  }),

  http.post(`${API_BASE_URL}/auth/logout`, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')?.startsWith('Bearer ')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    return new HttpResponse(null, { status: 204, headers: { 'Set-Cookie': `${MOCK_REFRESH_COOKIE}=; Path=/; Max-Age=0` } })
  }),
]
