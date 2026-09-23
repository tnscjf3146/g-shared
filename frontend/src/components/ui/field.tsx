import type { ReactNode } from 'react'

type Props = { id: string; label: string; error?: string; hint?: ReactNode; children: ReactNode }

// 피그마 Field: 라벨(13, #5e5c63) → 입력 → 오류 문구(13, danger). 라벨-입력 gap 6. 오류는 aria-describedby 로 연결
export function Field({ id, label, error, hint, children }: Props) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <label htmlFor={id} className="text-caption font-medium text-text-muted">
          {label}
        </label>
        {hint}
      </div>
      {children}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-caption text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
