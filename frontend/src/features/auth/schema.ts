import { z } from 'zod'

// 서버 검증 규칙(back SignupRequest)과 동일하게 — 메시지도 같은 문장
export const emailSchema = z.email({ message: '이메일 형식이 아닙니다.' }).min(1, '이메일은 필수값입니다.')
export const passwordSchema = z
  .string()
  .min(1, '비밀번호는 필수값입니다.')
  .min(6, '비밀번호는 6 ~ 20자입니다.')
  .max(20, '비밀번호는 6 ~ 20자입니다.')
  .regex(/^(?=.*[a-zA-Z])(?=.*\d).+$/, '비밀번호는 영문과 숫자를 포함해야 합니다.')

export const nicknameSchema = z
  .string()
  .min(1, '닉네임은 필수값입니다.')
  .min(2, '닉네임은 2 ~ 15자 입니다.')
  .max(15, '닉네임은 2 ~ 15자 입니다.')
  .regex(/^[a-zA-Z0-9가-힣]+$/, '닉네임은 영문, 숫자, 한글만 사용할 수 있습니다.')

// 비밀번호 확인은 프론트 전용 필드 — 서버 요청 본문에는 email/password/nickname 만 보낸다
export const signupSchema = z
  .object({
    nickname: nicknameSchema,
    email: emailSchema,
    password: passwordSchema,
    passwordConfirm: z.string().min(1, '비밀번호를 한 번 더 입력해 주세요.'),
  })
  .refine((v) => v.password === v.passwordConfirm, { path: ['passwordConfirm'], message: '비밀번호가 일치하지 않습니다.' })

export const loginSchema = z.object({
  email: emailSchema,
  password: z.string().min(1, '비밀번호는 필수값입니다.'), // 로그인은 형식 검사 없이 존재만 — 규칙 노출을 피한다
})

export const tokenResponseSchema = z.object({ accessToken: z.string().min(1) })
