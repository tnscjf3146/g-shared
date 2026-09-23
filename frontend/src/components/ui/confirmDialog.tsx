'use client'

import { useEffect, useRef } from 'react'
import { Button } from './button'

// 확인 한 줄 + [취소][확인] — 네이티브 <dialog>(showModal) 라 Esc·포커스 가두기·배경 막기는 브라우저가 처리. L4(모달) 레이어
type Props = { open: boolean; message: string; confirmLabel?: string; danger?: boolean; pending?: boolean; onConfirm: () => void; onCancel: () => void }

export function ConfirmDialog({ open, message, confirmLabel = '확인', danger = false, pending = false, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onCancel}
      onKeyDown={(e) => e.key === 'Escape' && onCancel()} // Esc = 취소(네이티브 cancel 이 안 오는 환경 대비)
      onClick={(e) => e.target === e.currentTarget && onCancel()} // 배경(backdrop) 클릭 = 취소
      className="animate-enter-spring-l4 m-auto w-[min(92vw,380px)] layer-4 rounded-lg border border-hairline p-6 text-dark backdrop:bg-black/40 open:flex open:flex-col open:gap-5"
    >
      <p className="text-body font-semibold">{message}</p>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel} disabled={pending}>
          취소
        </Button>
        <Button variant={danger ? 'inverse' : 'primary'} onClick={onConfirm} disabled={pending}>
          {pending ? '처리 중…' : confirmLabel}
        </Button>
      </div>
    </dialog>
  )
}
