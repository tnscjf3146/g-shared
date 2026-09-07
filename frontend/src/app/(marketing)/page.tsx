import { HeroSection } from './_components/hero/heroSection'
import { LandingScene } from './_components/landing/landingScene'
import { BackToTop } from './_components/backToTop'
import { SiteFooter } from './_components/siteFooter'
import { SiteNav } from './_components/siteNav'

// 랜딩 = 고정 네비(별도 레이어) + 밝은 Hero → GPU 모션(검정 sticky, anime.js 챕터) → 어두운 Footer. 구성 근거: _components/landing/landing.md §0-1
export default function LandingPage() {
  return (
    <main className="flex-1 bg-black">
      <SiteNav />
      <HeroSection />
      <LandingScene />
      <SiteFooter />
      <BackToTop />
    </main>
  )
}
