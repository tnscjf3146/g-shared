import type { ComponentProps } from 'react'

// 입력 규칙은 AGENTS.md "버튼" 절(입력도 같은 8px 모서리), 스타일은 globals.css 의 input / input-lg
// 네이티브 input 의 size(글자 수) 속성은 쓰지 않으므로 같은 이름을 높이 크기(md 40 / lg 46)로 재정의한다
type Props = Omit<ComponentProps<'input'>, 'size'> & { size?: 'md' | 'lg' }

export function Input({ className = '', size = 'md', ...props }: Props) {
  return <input className={`input ${size === 'lg' ? 'input-lg' : ''} ${className}`} {...props} />
}
