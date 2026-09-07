'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { login } from '../api'
import { messageFor } from '../errors'
import { useZodForm } from '../hooks/use-zod-form'
import { loginSchema } from '../schema'
import { useAuth } from '../store'

// 피그마 P2 로그인 · LoginCard (지갑 연결 버튼은 지갑 기능 브랜치에서). 제출 시 zod 검사 → 필드 오류, 서버 오류는 폼 상단 인라인
export function LoginForm() {
  const router = useRouter()
  const params = useSearchParams()
  const { status } = useAuth()
  const f = useZodForm(loginSchema, { email: '', password: '' })
  const [showPw, setShowPw] = useState(false)

  const next = params.get('next')
  const nextPath = next && next.startsWith('/') ? next : '/'
  const expired = params.get('reason') === 'expired' // 보호 API 에서 만료로 돌려보내진 경우
  const signedUp = params.get('signup') === 'done' // 회원가입 직후

  // 이미 로그인된 상태로 오면 통과
  useEffect(() => {
    if (status === 'authed') router.replace(nextPath)
  }, [status, router, nextPath])

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault()
    f.setFormError(null)
    const data = f.validate()
    if (!data) return
    f.setPending(true)
    try {
      await login(data)
      router.replace(nextPath)
    } catch (err) {
      f.setFormError(messageFor(err))
      f.setPending(false)
    }
  }

  const notice = f.formError ?? (expired ? '다시 로그인해 주세요.' : null)

  return (
    <form onSubmit={submit} noValidate className="card flex w-[440px] max-w-full flex-col gap-5 p-10">
      <span className="font-logo text-[32px] font-semibold text-dark">G-shared</span>

      {notice && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[14px] text-danger">
          {notice}
        </p>
      )}
      {signedUp && !notice && (
        <p role="status" className="rounded-md bg-success-tint px-4 py-3 text-[14px] text-success">
          가입이 완료됐습니다. 로그인해 주세요.
        </p>
      )}

      <Field id="email" label="이메일" error={f.errors.email}>
        <Input
          id="email"
          size="lg"
          type="email"
          autoComplete="email"
          placeholder="email"
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
          type={showPw ? 'text' : 'password'}
          autoComplete="current-password"
          placeholder="password"
          value={f.values.password}
          onChange={(e) => f.set('password', e.target.value)}
          aria-invalid={!!f.errors.password}
          aria-describedby={f.describedBy('password')}
        />
      </Field>

      <Button type="submit" size="lg" className="w-full" disabled={f.pending}>
        {f.pending && <span aria-hidden className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
        {f.pending ? '로그인 중…' : '로그인'}
      </Button>

      <Link href="/signup" className="text-[14px] text-primary hover:underline">
        계정이 없으신가요? 회원가입 →
      </Link>
    </form>
  )
}
