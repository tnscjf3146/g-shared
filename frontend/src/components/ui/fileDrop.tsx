'use client'

import { useId, useRef, useState } from 'react'

// 파일 드롭 영역 — 끌어놓기 또는 클릭(네이티브 <input type=file>). 파일은 부모 상태(File)로만 들고, 업로드는 하지 않는다
type Props = {
  file: File | null
  onFile: (file: File | null) => void
  accept?: string
  label: string
  hint?: string
  disabled?: boolean
}

export function FileDrop({ file, onFile, accept, label, hint, disabled = false }: Props) {
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const pick = (list: FileList | null) => {
    const f = list?.[0] ?? null
    if (f) onFile(f)
  }

  if (file) {
    return (
      <div className="card-quiet flex items-center justify-between gap-4 p-4">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 2H4.5A1.5 1.5 0 0 0 3 3.5v9A1.5 1.5 0 0 0 4.5 14h7a1.5 1.5 0 0 0 1.5-1.5V6z" />
              <path d="M9 2v4h4" />
            </svg>
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-[14px] font-semibold text-dark" title={file.name}>
              {file.name}
            </span>
            <span className="text-[12px] tabular-nums text-text-muted">{formatSize(file.size)}</span>
          </div>
        </div>
        <button type="button" onClick={() => onFile(null)} disabled={disabled} className="shrink-0 text-[13px] text-text-muted hover:text-danger disabled:opacity-60">
          제거
        </button>
      </div>
    )
  }

  return (
    <label
      htmlFor={id}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        if (!disabled) pick(e.dataTransfer.files)
      }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
        over ? 'border-primary bg-primary/5' : 'border-hairline-strong bg-surface-1 hover:border-border'
      } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
    >
      <svg aria-hidden width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="text-text-muted">
        <path d="M12 16V4m0 0-4 4m4-4 4 4" />
        <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
      </svg>
      <span className="text-[14px] font-semibold text-dark">{label}</span>
      {hint && <span className="text-[12px] text-text-muted">{hint}</span>}
      <input id={id} ref={input} type="file" accept={accept} disabled={disabled} className="sr-only" onChange={(e) => pick(e.target.files)} />
    </label>
  )
}

function formatSize(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}
