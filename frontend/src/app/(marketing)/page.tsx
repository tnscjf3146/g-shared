import { LandingScene } from './_components/landing/landingScene'
import { BackToTop } from './_components/backToTop'
import { SiteFooter } from './_components/siteFooter'
import { SiteNav } from './_components/siteNav'

// 랜딩 = 고정 네비(별도 레이어) + GPU 모션(검정 sticky, anime.js 챕터 — 첫 화면, 인트로는 모델 로드 뒤 자동 재생) → 어두운 Footer. Hero 는 2026-09-10 제거. 구성 근거: _components/landing/landing.md §0-1
export default function LandingPage() {
  return (
    <main className="flex-1 bg-black">
      <SiteNav />
      <LandingScene />
      <SiteFooter />
      <BackToTop />
    </main>
  )
}
