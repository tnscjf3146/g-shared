'use client'

import { animate, createScope, createTimeline } from 'animejs'
import dynamic from 'next/dynamic'
import { useEffect, useRef } from 'react'
import { chapterStore } from './chapter-store'
import { ChapterSidebar } from './chapterSidebar'
import { CHAPTERS, assemble, cameraTrack, fansOut, hero, hsLift, hsOff, introTop, reveal, shroudOff, smoke, stack, topView } from './motion/chapters'
import { GPU_READY_EVENT } from './progress'

const GpuCanvas = dynamic(() => import('./three/gpuCanvas').then((m) => m.GpuCanvas), { ssr: false })

// 섹션 DOM + 타임라인 두 개(인트로: 시간, 스크롤: 챕터 단위 재생)를 만들고 motion/chapters.ts 의 챕터 함수를 얹는다.
// 이 섹션이 페이지 첫 화면(Hero 없음, 2026-09-10). 인트로는 GLB 로드 신호(GPU_READY_EVENT)를 받은 뒤 1회 재생되며 그동안 스크롤을 잠근다.
// 스크롤 연출은 스크럽(스크롤 위치 = 타임라인 시각)이 아니라 **챕터 스텝**: 스크롤이 챕터 시작 지점을 넘으면 그 챕터가 제 속도로 끝까지 재생되고,
// 되돌아 올라가면 이전 챕터 끝으로 되감긴다(2026-09-10 사용자 결정).
// 스크롤 구간은 **균등 분할**(카드 + 챕터, 지금 7개): 한 챕터당 SCROLL_PER_CHAPTER_VH 만큼 스크롤하면 다음 트리거(2026-09-11, 이전엔 타임라인 시각 비례라 간격이 들쭉날쭉).
// 오른쪽 챕터 사이드바(chapterSidebar)가 현재 챕터를 보여 주고 클릭하면 그 챕터 위치로 점프한다(chapter-store). 스크롤 입력 자체는 가로채지 않는다
const READY_TIMEOUT_MS = 8000 // 모델 로드 신호가 안 오면(네트워크·디코더 실패) 이 시간 뒤엔 잠금을 풀고 인트로를 그냥 진행
const BANDS = CHAPTERS.length + 1 // 스크롤 구간 수 = 카드(0) + 챕터 수(chapters.ts CHAPTERS)
const SCROLL_PER_CHAPTER_VH = 150 // 챕터 하나당 스크롤 거리(뷰포트 높이 배수). 100 → 150(2026-09-11, 반 화면 더 길게). 섹션 높이 = sticky 100vh + BANDS × 이 값
const STEP_MIN = 1300 // 사용자 요청(2026-09-10)으로 한 단계 느리게: 900/1800/0.6 → 1300/2800/0.9
const STEP_MAX = 2800
const STEP_RATE = 0.9 // 타임라인 1ms 당 실제 0.9ms
const REDUCED_RATE = 0.35 // prefers-reduced-motion 일 때의 재생 시간 배율(정지가 아니라 빠르게). 윈도우 "애니메이션 효과" 끄기(PC방 기본값)가 이 미디어쿼리로 오므로 완전 정지시키면 화면이 안 움직인다(2026-09-14)

export function LandingScene() {
  const root = useRef<HTMLElement>(null)

  useEffect(() => {
    const el = root.current
    if (!el) return
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const rate = reduce ? REDUCED_RATE : 1 // 모든 지속 시간에 곱해진다
    const scope = createScope({ root: el }).add(() => {
      // 스크롤 타임라인 (챕터 3~). autoplay 없이 seek 로만 움직인다. composition:'none' — 같은 P 속성을 만지는 인트로 타임라인이 이쪽 트윈을 취소하지 않게
      const tl = createTimeline({ defaults: { ease: 'inOut(2)', composition: 'none' }, autoplay: false })
      cameraTrack(tl) // 카메라는 한 트랙으로(전 구간 빈틈 없음)
      fansOut(tl)
      smoke(tl)
      shroudOff(tl)
      hsLift(tl)
      hsOff(tl)
      topView(tl)
      stack(tl)
      assemble(tl)

      // 스크롤 위치 → 섹션 진행률(0..1) → 타임라인 시각(ms). sticky 스테이지가 화면을 채운 동안만 0..1
      const fraction = () => {
        const r = el.getBoundingClientRect()
        return Math.min(1, Math.max(0, -r.top / (r.height - innerHeight)))
      }
      // 진행률 f(0..1) → 구간 번호(0..7, 균등) → 그 챕터의 타임라인 끝 시각
      const targetFor = (f: number) => {
        const index = Math.min(CHAPTERS.length, Math.floor(f * BANDS))
        const c = index > 0 ? CHAPTERS[index - 1] : null
        return { to: c?.to ?? 0, at: c?.at ?? 0, index, fixed: c?.duration, ease: c?.ease }
      }
      // 챕터 스텝: 목표 시각이 바뀌면 현재 시각에서 목표까지 제 속도로 재생(되감기 포함). 같은 cursor 를 다시 animate 하면 앞의 것은 교체된다
      const cursor = { t: 0 }
      let current = -1
      let introPlaying = false
      const step = (instant = false) => {
        const { to, at, index, fixed, ease } = targetFor(fraction())
        if (index === current) return
        current = index
        el.dataset.chapter = String(index) // 디버그·검증용(어느 챕터까지 재생됐는지)
        if (!introPlaying) chapterStore.set(index) // 사이드바 현재 표시
        if (instant) {
          cursor.t = to
          tl.seek(to)
          return
        }
        // 앞으로 갈 때는 챕터의 첫 동작 시각(at)까지의 공백(카메라 정지 구간)을 건너뛰고 바로 동작부터 재생 — 도착 즉시 발동(2026-09-11)
        if (to > cursor.t && cursor.t < at) {
          cursor.t = at
          tl.seek(at)
        }
        const duration = (fixed ?? Math.min(STEP_MAX, Math.max(STEP_MIN, Math.abs(to - cursor.t) * STEP_RATE))) * rate
        // ease 는 out: 트리거를 넘거나 사이드바를 누른 순간 바로 움직이기 시작하고 끝에서 감속(inOut 은 초반 30% 가 거의 멈춰 있어 느리게 느껴졌다, 2026-09-11)
        animate(cursor, { t: to, duration, ease: ease ?? 'out(3)', onUpdate: () => tl.seek(cursor.t) })
      }
      // 사이드바 클릭 → 챕터 i 의 스크롤 위치로: 구간 i 의 시작 바로 안쪽. 캔버스는 sticky 라 점프가 보이지 않고 step() 이 애니메이션을 맡는다
      const yFor = (i: number) => el.offsetTop + (i === 0 ? 0 : i / BANDS + 0.002) * (el.offsetHeight - innerHeight)
      chapterStore.setJumper((i) => {
        if (introPlaying) return
        scrollTo({ top: yFor(Math.min(CHAPTERS.length, Math.max(0, i))), behavior: 'auto' })
        step()
      })

      // 인트로 타임라인 (챕터 0~2, 시간 기반) — 스크롤 tl 뒤에 만들어 첫 프레임 우선권을 가진다.
      // 페이지 로드 직후 스크롤을 잠그고, 모델이 준비되면 1회 재생한다(빈 검정 화면에 빔만 지나가는 일이 없게).
      const intro = createTimeline({ defaults: { ease: 'inOut(2)', composition: 'none' }, autoplay: false, playbackRate: 1 / rate }) // reduced-motion 이면 생략이 아니라 배속 재생
      introTop(intro)
      hero(intro)
      reveal(intro)
      const skip = () => intro.complete()
      // 네비 표시 규칙: 인트로 동안 숨김 → 인트로가 끝난 맨 위(스크롤 0)에서 보임 → 스크롤 스토리 내내 숨김 → 마지막 챕터가 끝나 Footer 가 보이면 다시 보임.
      // 배경이 전부 어두우니 테마는 dark 고정. 상태는 body[data-nav] 로 넘기고 실제 슬라이드는 siteNav 의 CSS 가 담당(리렌더 0)
      const setNav = (shown: boolean) => (document.body.dataset.nav = shown ? 'shown' : 'hidden')
      document.body.dataset.navTheme = 'dark'
      const updateNav = () => {
        const f = fraction()
        // 배경판은 차콜 한 장 고정(2026-09-14 사용자 결정) — 스크롤로 기록하던 P.scroll/plate/plate2W/light 을 전부 제거했다. 여긴 네비 규칙만 남는다
        const atEnd = f >= 0.985
        setNav((f <= 0.001 && !introPlaying) || atEnd)
        document.body.dataset.atEnd = atEnd ? '1' : '0' // "맨 위로" 버튼은 끝 구간에서만(globals.css .back-to-top)
      }
      // 스크롤 핸들러(rAF 로 묶음): 챕터 스텝 + 네비
      let raf = 0
      const onScrollTick = () => {
        raf = 0
        step()
        updateNav()
      }
      const onScroll = () => {
        if (!raf) raf = requestAnimationFrame(onScrollTick)
      }
      addEventListener('scroll', onScroll, { passive: true })
      addEventListener('resize', onScroll)

      // 인트로 동안 스크롤 잠금: 휠·키보드는 overflow:hidden, 모바일 터치는 touchmove 차단. 인트로가 끝나면 해제
      const html = document.documentElement
      const block = (ev: Event) => ev.preventDefault()
      const lock = () => {
        scrollTo({ top: el.offsetTop, behavior: 'auto' }) // 챕터 스텝이 정확히 0 에서 시작하도록 섹션 상단에 맞춤. 'auto' = 즉시(html 에 scroll-behavior:smooth 없음). 'instant' 는 표준이지만 옛 엔진에서 TypeError 위험
        introPlaying = true
        chapterStore.set(-1) // 인트로 동안 사이드바 숨김
        setNav(false) // 인트로 동안 네비 숨김
        html.style.overflow = 'hidden'
        addEventListener('touchmove', block, { passive: false })
      }
      const unlock = () => {
        html.style.overflow = ''
        removeEventListener('touchmove', block)
        introPlaying = false
        chapterStore.set(current) // 사이드바 표시
        updateNav() // 인트로가 끝난 맨 위 → 네비 슬라이드 인
      }
      let readyTimer: ReturnType<typeof setTimeout> | undefined
      const start = () => {
        removeEventListener(GPU_READY_EVENT, start)
        clearTimeout(readyTimer)
        intro.play()
        intro.then(unlock)
      }
      // 이미 챕터 구간까지 스크롤된 채 진입(새로고침 등)이면 그 챕터 상태로 바로 맞추고 인트로 생략. 아니면 바로 잠그고 모델 로드 신호를 기다린다
      const startedMid = fraction() * BANDS >= 1
      step(true)
      updateNav()
      if (startedMid) skip()
      else {
        lock()
        addEventListener(GPU_READY_EVENT, start)
        readyTimer = setTimeout(start, READY_TIMEOUT_MS)
      }
      return () => {
        removeEventListener(GPU_READY_EVENT, start)
        clearTimeout(readyTimer)
        unlock()
        chapterStore.setJumper(null)
        chapterStore.set(-1)
        removeEventListener('scroll', onScroll)
        removeEventListener('resize', onScroll)
        cancelAnimationFrame(raf)
        delete document.body.dataset.nav
        delete document.body.dataset.navTheme
        delete document.body.dataset.atEnd
      }
    })
    return () => scope.revert()
  }, [])

  return (
    <section ref={root} style={{ height: `${100 + BANDS * SCROLL_PER_CHAPTER_VH}vh` }} className="relative bg-black text-white">
      {/* 바닥 검정 + 배경은 캔버스 안 차콜 금속판 한 장 고정(three/backdrop.tsx kind="shroud") — 로고가 화면 정중앙에 박혀 있고 스크롤해도 흐르지 않는다(2026-09-14 사용자 결정).
          밝기는 씬 조명(P.env)만 따르므로 암전인 인트로에선 검정. 검정 와이프·스틸판 조명(kind black/lit)과 별(three/starfield)·DOM 배경 레이어는 마운트하지 않는다 */}
      <div className="sticky top-0 h-screen overflow-hidden bg-black">
        <GpuCanvas />
        {/* 인트로 암전용 검은 오버레이(팬 컷 → 로고 컷 사이). 인트로 타임라인이 opacity 를 움직인다 */}
        <div id="blackout" className="pointer-events-none absolute inset-0 bg-black opacity-0" />
        {/* 오른쪽 챕터 목록 — 호버 시 초록·왼쪽으로 밀림, 현재 챕터 상시 강조, 클릭으로 점프. 인트로 중엔 숨김 */}
        <ChapterSidebar />
      </div>
    </section>
  )
}
