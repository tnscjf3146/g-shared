import type { ComponentProps } from 'react'

// 네이티브 <select> 래퍼 — 커스텀 드롭다운을 만들지 않는다. 입력(input 유틸)과 같은 8px 모서리·높이 40, 오른쪽에 화살표만 그린다
type Props = Omit<ComponentProps<'select'>, 'size'> & { size?: 'md' | 'lg' }

export function Select({ className = '', size = 'md', children, ...props }: Props) {
  return (
    <div className="relative">
      <select className={`input appearance-none pr-9 ${size === 'lg' ? 'input-lg' : ''} ${className}`} {...props}>
        {children}
      </select>
      <svg aria-hidden className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-text-muted" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="m4 6 4 4 4-4" />
      </svg>
    </div>
  )
}
