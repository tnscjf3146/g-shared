import { Button } from '@/components/ui/button'
import { HeroArt } from './heroArt'

// 랜딩 첫 화면 (피그마 P1 랜딩 v3 · Hero, 밝은 배경). 서버 컴포넌트 — 스크롤 연출과 무관한 일반 섹션. 네비는 page.tsx 의 별도 고정 레이어(SiteNav).
// 이 아래에 <LandingScene/>(검정 sticky 3D)가 이어지므로 하단은 자연스럽게 어두운 섹션으로 넘어간다.
export function HeroSection() {
  return (
    <section className="relative min-h-screen overflow-hidden bg-white text-black">
      {/* 장식 블러 원 (피그마 초록 원, 밝은 배경에선 옅게) */}
      <div aria-hidden className="pointer-events-none absolute -top-24 -left-24 h-[420px] w-[420px] rounded-full bg-primary/10 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute top-1/3 right-[-160px] h-[520px] w-[520px] rounded-full bg-accent-mint/10 blur-3xl" />
      <div aria-hidden className="pointer-events-none absolute -bottom-32 left-1/3 h-[360px] w-[360px] rounded-full bg-primary/15 blur-3xl" />

      <div className="mx-auto flex min-h-screen max-w-[1440px] items-center justify-between gap-16 px-16 pt-24 pb-16">
        <div className="flex max-w-[600px] flex-col gap-6">
          <p className="font-heading text-base font-extrabold tracking-[0.2em] text-primary">DECENTRALIZED GPU SHARING</p>
          <h1 className="font-heading text-[56px] leading-[1.15] font-extrabold text-black">
            잠자는 그래픽카드가
            <br />
            누군가의 AI가 됩니다
          </h1>
          <p className="text-[19px] leading-relaxed text-dark">
            전국의 유휴 GPU를 연결해 클라우드보다 저렴하게 학습을 돌리고, 정산은 블록체인이 증명합니다.
          </p>
          <div className="flex items-center gap-4 pt-2">
            <Button href="/" size="lg">지금 시작하기</Button>
            <Button href="/" variant="ghost" size="lg">
              내 GPU로 수익 내기
            </Button>
          </div>
        </div>
        <div className="hidden shrink-0 lg:block">
          <HeroArt />
        </div>
      </div>
    </section>
  )
}
