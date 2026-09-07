'use client'

import { createScope, createTimeline, onScroll } from 'animejs'
import dynamic from 'next/dynamic'
import { useEffect, useRef } from 'react'
import { assemble, cameraTrack, fansOut, hero, hsLift, hsOff, introTop, reveal, shroudOff, smoke, stack, topView } from './motion/chapters'
import { P } from './progress'

const GpuCanvas = dynamic(() => import('./three/gpuCanvas').then((m) => m.GpuCanvas), { ssr: false })

// 섹션 DOM + 타임라인 두 개(인트로: 시간, 스크롤: sync)를 만들고 motion/chapters.ts 의 챕터 함수를 얹는다.
// 스크롤이 시작되면 인트로는 즉시 끝 상태로 점프 → 스크롤 tl 이 이어받음.
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

      // 인트로 타임라인 (챕터 1~2, 시간 기반) — 스크롤 tl 뒤에 만들어 첫 프레임 우선권을 가진다
      const intro = createTimeline({ defaults: { ease: 'inOut(2)', composition: 'none' } })
      introTop(intro)
      hero(intro)
      reveal(intro)
      const skip = () => intro.complete()
      if (reduce || scrollY > 0) skip()
      addEventListener('scroll', skip, { once: true, passive: true })
      if (reduce) P.spin = 0
      return () => removeEventListener('scroll', skip)
    })
    return () => scope.revert()
  }, [])

  return (
    <section ref={root} className="relative h-[2400vh] bg-black text-white">
      <div className="sticky top-0 h-screen overflow-hidden">
        <GpuCanvas />
        {/* 인트로 암전용 검은 오버레이(팬 컷 → 로고 컷 사이). 인트로 타임라인이 opacity 를 움직인다 */}
        <div id="blackout" className="pointer-events-none absolute inset-0 bg-black opacity-0" />
      </div>
    </section>
  )
}
