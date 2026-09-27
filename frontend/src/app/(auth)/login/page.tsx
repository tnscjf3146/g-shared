import type { Metadata } from 'next'
import { Suspense } from 'react'
import { LoginForm } from '@/features/auth/components/loginForm'
import { AuthBackdrop } from '../_components/authBackdrop'

export const metadata: Metadata = { title: '로그인 — G-shared' }

// LoginForm 이 useSearchParams(next/reason/signup) 를 쓰므로 Suspense 경계 필요
export default function LoginPage() {
  return (
    <>
      <AuthBackdrop variant="login" />
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </>
  )
}
