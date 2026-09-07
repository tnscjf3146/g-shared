import type { z } from 'zod'
import type { loginSchema, signupSchema } from './schema'

export type LoginInput = z.infer<typeof loginSchema>
export type SignupRequest = Pick<z.infer<typeof signupSchema>, 'email' | 'password' | 'nickname'> // 서버 본문(비밀번호 확인은 프론트 전용)

export type AuthState = { status: 'loading' | 'authed' | 'guest'; accessToken: string | null }
