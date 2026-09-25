'use client'

import Link from 'next/link'
import { toastStore, useToasts, type ToastTone } from '@/lib/stores/toast-store'

// 토스트 표시부 — 스토어에 쌓인 것을 화면 우하단에 그린다. 띄우는 쪽은 toastStore.push 만 부르면 된다.
// AGENTS.md: 토스트는 L4(가장 위) + 스프링 등장 곡선. 접근성 규칙상 완료 알림은 aria-live 를 지원해야 한다.

const TONE: Record<ToastTone, string> = {
  success: 'border-primary/45 bg-surface-2',
  danger: 'border-danger/45 bg-surface-2',
  neutral: 'border-hairline bg-surface-2',
}
const MARK: Record<ToastTone, string> = { success: 'text-primary', danger: 'text-danger', neutral: 'text-text-muted' }

export function Toaster() {
  const toasts = useToasts()
  return (
    // 항상 DOM 에 두고 비어 있을 때만 내용이 없다 — 나중에 생기는 노드는 스크린리더가 못 읽는 경우가 있어
    // live region 은 처음부터 존재해야 한다. pointer-events-none 으로 빈 영역이 클릭을 가로채지 않게
    <div aria-live="polite" aria-atomic="false" className="pointer-events-none fixed right-4 bottom-4 z-40 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`animate-enter-spring-l4 pointer-events-auto flex max-w-[380px] items-start gap-3 rounded-lg border p-4 shadow-4 ${TONE[t.tone]}`}
        >
          <span aria-hidden className={`text-body leading-none font-bold ${MARK[t.tone]}`}>
            {t.tone === 'danger' ? '!' : '✓'}
          </span>
          <div className="flex min-w-0 flex-col gap-2">
            <p className="text-caption text-dark">{t.message}</p>
            {t.action && (
              <Link href={t.action.href} onClick={() => toastStore.dismiss(t.id)} className="text-caption font-semibold text-primary hover:underline">
                {t.action.label} →
              </Link>
            )}
          </div>
          <button
            type="button"
            onClick={() => toastStore.dismiss(t.id)}
            aria-label="알림 닫기"
            className="ml-auto flex-none rounded-xs px-1 text-caption text-text-muted hover:text-dark"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
