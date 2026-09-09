// 고정 fixture — 개인정보처럼 보이는 실제 값 금지. 로그인은 서버(LoginRequest)도 프론트(loginSchema)도 이메일 형식을 강제하지 않아 짧은 아이디를 쓴다
export const MOCK_USER = { email: 'user1', password: '1234', nickname: '유저일' }
export const MOCK_ACCESS_TOKEN = 'mock-access-token'
export const MOCK_REFRESH_COOKIE = 'refreshToken'
