'use client'

import { useSyncExternalStore } from 'react'

// "맨 위로" — 푸터 위에 떠 있는 원형 화살표 버튼(아이콘만). 스크롤을 내리기 시작하면 나타나고 맨 위에선 숨는다.
// 위치는 globals.css .back-to-top. 클릭 시 부드럽게 0 으로(연출은 역재생)
const subscribe = (cb: () => void) => {
  addEventListener('scroll', cb, { passive: true })
  return () => removeEventListener('scroll', cb)
}
const isScrolled = () => scrollY > 80

export function BackToTop() {
  const shown = useSyncExternalStore(subscribe, isScrolled, () => false)
  return (
    <button
      type="button"
      aria-label="맨 위로"
      tabIndex={shown ? undefined : -1}
      onClick={() => scrollTo({ top: 0, behavior: 'smooth' })}
      className={`back-to-top inline-flex h-12 w-12 items-center justify-center rounded-pill border border-white/25 bg-white/10 text-white backdrop-blur-md transition-[opacity,background-color] duration-300 hover:bg-white/20 ${shown ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
    >
      <svg aria-hidden width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" />
      </svg>
    </button>
  )
}
