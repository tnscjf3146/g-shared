'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { signup } from '../api'
import { messageFor } from '../errors'
import { useZodForm } from '../hooks/use-zod-form'
import { signupSchema } from '../schema'

// 피그마 P3 회원가입 · SignupCard. 비밀번호 확인은 프론트에서만 검사(서버 요청엔 email/password/nickname 만). 약관 동의는 백엔드가 받지 않아 두지 않는다
export function SignupForm() {
  const router = useRouter()
  const f = useZodForm(signupSchema, { nickname: '', email: '', password: '', passwordConfirm: '' })
  const [showPw, setShowPw] = useState(false)

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault()
    f.setFormError(null)
    const data = f.validate()
    if (!data) return
    f.setPending(true)
    try {
      await signup({ email: data.email, password: data.password, nickname: data.nickname })
      router.push('/login?signup=done')
    } catch (err) {
      f.setFormError(messageFor(err))
      f.setPending(false)
    }
  }
  const pwType = showPw ? 'text' : 'password'

  return (
    <form onSubmit={submit} noValidate className="card flex w-[440px] max-w-full flex-col gap-[18px] p-10">
      <div className="flex flex-col gap-2 text-center">
        <span className="font-logo text-[32px] font-semibold text-dark">G-shared</span>
        <p className="text-[15px] text-text-muted">계정을 만들고 GPU를 쓰거나, 공유해 보세요</p>
      </div>

      {f.formError && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[14px] text-danger">
          {f.formError}
        </p>
      )}

      <Field id="nickname" label="닉네임" error={f.errors.nickname}>
        <Input
          id="nickname"
          size="lg"
          autoComplete="nickname"
          placeholder="2~15자, 영문·숫자·한글"
          value={f.values.nickname}
          onChange={(e) => f.set('nickname', e.target.value)}
          aria-invalid={!!f.errors.nickname}
          aria-describedby={f.describedBy('nickname')}
        />
      </Field>
      <Field id="email" label="이메일" error={f.errors.email}>
        <Input
          id="email"
          size="lg"
          type="email"
          autoComplete="email"
          placeholder="gshared@gshared.com"
          value={f.values.email}
          onChange={(e) => f.set('email', e.target.value)}
          aria-invalid={!!f.errors.email}
          aria-describedby={f.describedBy('email')}
        />
      </Field>
      <Field
        id="password"
        label="비밀번호"
        error={f.errors.password}
        hint={
          <button type="button" onClick={() => setShowPw((s) => !s)} className="text-[13px] text-text-muted hover:text-dark">
            {showPw ? '숨기기' : '보기'}
          </button>
        }
      >
        <Input
          id="password"
          size="lg"
          type={pwType}
          autoComplete="new-password"
          placeholder="6~20자, 영문+숫자"
          value={f.values.password}
          onChange={(e) => f.set('password', e.target.value)}
          aria-invalid={!!f.errors.password}
          aria-describedby={f.describedBy('password')}
        />
      </Field>
      <Field id="passwordConfirm" label="비밀번호 확인" error={f.errors.passwordConfirm}>
        <Input
          id="passwordConfirm"
          size="lg"
          type={pwType}
          autoComplete="new-password"
          value={f.values.passwordConfirm}
          onChange={(e) => f.set('passwordConfirm', e.target.value)}
          aria-invalid={!!f.errors.passwordConfirm}
          aria-describedby={f.describedBy('passwordConfirm')}
        />
      </Field>

      <Button type="submit" size="lg" className="w-full" disabled={f.pending}>
        {f.pending && <span aria-hidden className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
        {f.pending ? '만드는 중…' : '계정 만들기'}
      </Button>

      <Link href="/login" className="text-[14px] text-primary hover:underline">
        이미 계정이 있으신가요? 로그인 →
      </Link>
      <p className="text-[12px] text-text-muted">가입 후 지갑을 연결하면 예치·정산 기능이 활성화됩니다.</p>
    </form>
  )
}
