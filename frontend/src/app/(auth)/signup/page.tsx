import type { Metadata } from 'next'
import { SignupForm } from '@/features/auth/components/signupForm'
import { AuthBackdrop } from '../_components/authBackdrop'

export const metadata: Metadata = { title: '회원가입 — G-shared' }

export default function SignupPage() {
  return (
    <>
      <AuthBackdrop variant="signup" />
      <SignupForm />
    </>
  )
}
