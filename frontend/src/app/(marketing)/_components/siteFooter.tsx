import Link from 'next/link'

// 랜딩 푸터 (피그마 P1 랜딩 v3 · Footer, 어두운 배경, 높이 80). 약관·개인정보 페이지는 아직 없어 홈으로 보낸다
export function SiteFooter() {
  return (
    <footer className="flex h-20 items-center justify-between bg-black px-16">
      <Link href="/" className="font-logo text-xl font-semibold text-white">
        G-shared
      </Link>
      <p className="text-[13px] text-text-muted">
        SSAFY 15기 특화 프로젝트 · E204 ·{' '}
        <Link href="/" className="transition-colors hover:text-white">
          이용약관
        </Link>{' '}
        ·{' '}
        <Link href="/" className="transition-colors hover:text-white">
          개인정보처리방침
        </Link>
      </p>
    </footer>
  )
}
