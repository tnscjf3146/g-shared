import * as z from 'zod/mini'

// zod/mini: 메서드 체인 대신 함수형 체크 — 쓰는 것만 번들에 들어가 zod 본체(약 370KB)가 전 페이지에 실리지 않는다
// 서버 검증 규칙(back SignupRequest)과 동일하게 — 메시지도 같은 문장
export const emailSchema = z.string().check(z.minLength(1, '이메일은 필수값입니다.'), z.email('이메일 형식이 아닙니다.'))
export const passwordSchema = z
  .string()
  .check(
    z.minLength(1, '비밀번호는 필수값입니다.'),
    z.minLength(6, '비밀번호는 6 ~ 20자입니다.'),
    z.maxLength(20, '비밀번호는 6 ~ 20자입니다.'),
    z.regex(/^(?=.*[a-zA-Z])(?=.*\d).+$/, '비밀번호는 영문과 숫자를 포함해야 합니다.'),
  )

export const nicknameSchema = z
  .string()
  .check(
    z.minLength(1, '닉네임은 필수값입니다.'),
    z.minLength(2, '닉네임은 2 ~ 15자 입니다.'),
    z.maxLength(15, '닉네임은 2 ~ 15자 입니다.'),
    z.regex(/^[a-zA-Z0-9가-힣]+$/, '닉네임은 영문, 숫자, 한글만 사용할 수 있습니다.'),
  )

// 비밀번호 확인은 프론트 전용 필드 — 서버 요청 본문에는 email/password/nickname 만 보낸다
export const signupSchema = z
  .object({
    nickname: nicknameSchema,
    email: emailSchema,
    password: passwordSchema,
    passwordConfirm: z.string().check(z.minLength(1, '비밀번호를 한 번 더 입력해 주세요.')),
  })
  .check(z.refine((v) => v.password === v.passwordConfirm, { path: ['passwordConfirm'], message: '비밀번호가 일치하지 않습니다.' }))

// 로그인은 두 필드 모두 형식 검사 없이 존재만 — 규칙 노출을 피하고, 서버(LoginRequest)도 형식을 보지 않는다(틀리면 INVALID_CREDENTIALS)
export const loginSchema = z.object({
  email: z.string().check(z.minLength(1, '이메일은 필수값입니다.')),
  password: z.string().check(z.minLength(1, '비밀번호는 필수값입니다.')),
})

export const tokenResponseSchema = z.object({ accessToken: z.string().check(z.minLength(1)) })
