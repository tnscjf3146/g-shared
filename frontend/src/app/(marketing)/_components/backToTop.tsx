'use client'

// "맨 위로" — 푸터 위에 떠 있는 고정 버튼. 스크롤 연출이 끝나 네비·푸터가 나오는 구간(body[data-nav=shown][data-nav-theme=dark])에만
// 보이도록 globals.css .back-to-top 이 표시를 제어한다. 모양은 네비 버튼(nav-btn, 어두운 변형)과 동일. 클릭 시 부드럽게 0 으로(연출은 역재생)
export function BackToTop() {
  return (
    <button type="button" onClick={() => scrollTo({ top: 0, behavior: 'smooth' })} className="nav-btn back-to-top">
      <svg aria-hidden width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" />
      </svg>
      맨 위로
    </button>
  )
}
