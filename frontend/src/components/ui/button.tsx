import Link from 'next/link'
import type { ComponentProps, ReactNode } from 'react'

// 버튼 규칙은 AGENTS.md "버튼" 절, 스타일은 globals.css 의 btn-* 유틸리티가 담당한다.
// 모서리 8px 고정(pill 금지), 크기 sm 32 / md 40 / lg 46, 변형 primary / secondary / ghost / inverse
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'inverse'
type ButtonSize = 'sm' | 'md' | 'lg'

// Tailwind v4 는 소스에 글자 그대로 있는 클래스만 CSS 로 뽑는다 → 템플릿 문자열(`btn-${size}`) 대신 정적 맵
const SIZE: Record<ButtonSize, string> = { sm: 'btn-sm', md: 'btn-md', lg: 'btn-lg' }
const VARIANT: Record<ButtonVariant, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  ghost: 'btn-ghost',
  inverse: 'btn-inverse',
}

type Props = ComponentProps<'button'> & { variant?: ButtonVariant; size?: ButtonSize; href?: string; children: ReactNode }

// href 가 있으면 링크로, 없으면 버튼으로 렌더. 서버 컴포넌트에서 그대로 쓸 수 있다('use client' 없음)
export function Button({ variant = 'primary', size = 'md', href, className = '', children, ...rest }: Props) {
  const cls = `btn ${SIZE[size]} ${VARIANT[variant]} ${className}`
  if (href) {
    return (
      <Link href={href} className={cls}>
        {children}
      </Link>
    )
  }
  return (
    <button type="button" className={cls} {...rest}>
      {children}
    </button>
  )
}
