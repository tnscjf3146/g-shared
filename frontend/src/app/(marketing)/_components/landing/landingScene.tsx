'use client'

import { createScope, createTimeline, onScroll } from 'animejs'
import dynamic from 'next/dynamic'
import { useEffect, useRef } from 'react'
import { assemble, cameraTrack, fansOut, hero, hsLift, hsOff, introTop, reveal, shroudOff, smoke, stack, topView } from './motion/chapters'
import { P } from './progress'

const GpuCanvas = dynamic(() => import('./three/gpuCanvas').then((m) => m.GpuCanvas), { ssr: false })

// 섹션 DOM + 타임라인 두 개(인트로: 시간, 스크롤: sync)를 만들고 motion/chapters.ts 의 챕터 함수를 얹는다.
// 인트로는 스테이지가 화면에 고정될 때 1회 재생되며 그동안 스크롤을 잠근다(끝나면 해제) → 이후 스크롤 tl 이 이어받음.
export function LandingScene() {
  const root = useRef<HTMLElement>(null)

  useEffect(() => {
    const el = root.current
    if (!el) return
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const scope = createScope({ root: el }).add(() => {
      // 스크롤 타임라인 (챕터 3~). composition:'none' — 기본값 'replace' 는 같은 P 속성을 만지는 다른 타임라인(인트로)이 생기면 이쪽 트윈을 취소해 버린다
      const tl = createTimeline({
        defaults: { ease: 'inOut(2)', composition: 'none' },
        autoplay: onScroll({
          target: el,
          enter: { container: 'top', target: 'top' },
          leave: { container: 'bottom', target: 'bottom' },
          sync: true,
        }),
      })
      cameraTrack(tl) // 카메라는 한 트랙으로(전 구간 빈틈 없음)
      fansOut(tl)
      smoke(tl)
      shroudOff(tl)
      hsLift(tl)
      hsOff(tl)
      topView(tl)
      stack(tl)
      assemble(tl)

      // 인트로 타임라인 (챕터 0~2, 시간 기반) — 스크롤 tl 뒤에 만들어 첫 프레임 우선권을 가진다.
      // 위에 Hero 섹션이 있으므로 로드 시가 아니라, sticky 스테이지가 뷰포트에 완전히 들어와 고정되는 순간 1회 재생한다.
      const intro = createTimeline({ defaults: { ease: 'inOut(2)', composition: 'none' }, autoplay: false })
      introTop(intro)
      hero(intro)
      reveal(intro)
      const skip = () => intro.complete()
      if (reduce) {
        skip()
        P.spin = 0
      }
      const stage = el.firstElementChild as HTMLElement
      // 네비 표시 규칙: Hero 위에선 보임 → 인트로 시작(스테이지 고정)에 위로 사라짐 → 스크롤 스토리 내내 숨김 → 마지막 챕터가 끝나 Footer 가 나올 때 다시 나옴.
      // 상태는 body[data-nav] 로 넘기고 실제 슬라이드는 siteNav 의 CSS 가 담당(리렌더 0)
      const setNav = (shown: boolean) => (document.body.dataset.nav = shown ? 'shown' : 'hidden')
      const updateNav = () => {
        const onHero = el.getBoundingClientRect().top > 0 // 섹션이 아직 고정되지 않음 = Hero 구간
        const atEnd = tl.progress >= 0.985 // ASSEMBLE 끝 = Footer 진입
        setNav(onHero || atEnd)
        document.body.dataset.navTheme = onHero ? 'light' : 'dark' // 밝은 Hero 위 / 검정 섹션·Footer 위 — 네비 유리 색 두 벌 중 선택
      }
      updateNav()
      addEventListener('scroll', updateNav, { passive: true })
      // 인트로 동안 스크롤 잠금: 휠·키보드는 overflow:hidden, 모바일 터치는 touchmove 차단. 인트로가 끝나면 해제
      const html = document.documentElement
      const block = (ev: Event) => ev.preventDefault()
      const lock = () => {
        scrollTo({ top: el.offsetTop, behavior: 'instant' }) // 스크롤 tl 이 정확히 0 에서 시작하도록 섹션 상단에 맞춤
        setNav(false) // 인트로 시작: 네비 위로 사라짐
        html.style.overflow = 'hidden'
        addEventListener('touchmove', block, { passive: false })
      }
      const unlock = () => {
        html.style.overflow = ''
        removeEventListener('touchmove', block)
      }
      const io = new IntersectionObserver(
        ([e]) => {
          if (!e.isIntersecting || e.intersectionRatio < 0.98) return
          io.disconnect()
          // 이미 챕터 구간까지 스크롤된 채 진입(새로고침 등)이면 인트로 생략
          if (tl.progress > 0.01) return skip()
          lock()
          intro.play()
          intro.then(unlock)
        },
        { threshold: [0.98] },
      )
      if (!reduce) io.observe(stage)
      return () => {
        io.disconnect()
        unlock()
        removeEventListener('scroll', updateNav)
        delete document.body.dataset.nav
        delete document.body.dataset.navTheme
      }
    })
    return () => scope.revert()
  }, [])

  return (
    <section ref={root} className="relative h-[2400vh] bg-dark text-white">
      <div className="sticky top-0 h-screen overflow-hidden bg-dark">
        {/* INTRO TOP 전용 딥 블랙 바닥(캔버스 뒤). 빔 스윕이 끝나고 암전이 덮이는 사이 인트로 타임라인이 opacity 1→0 으로 걷어내 이후는 bg-dark */}
        <div id="deep" className="pointer-events-none absolute inset-0 bg-black" />
        <GpuCanvas />
        {/* 인트로 암전용 검은 오버레이(팬 컷 → 로고 컷 사이). 인트로 타임라인이 opacity 를 움직인다 */}
        <div id="blackout" className="pointer-events-none absolute inset-0 bg-black opacity-0" />
      </div>
    </section>
  )
}
