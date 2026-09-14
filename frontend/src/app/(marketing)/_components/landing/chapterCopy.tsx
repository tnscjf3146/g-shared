'use client'

import { createTimeline, stagger, type Timeline } from 'animejs'
import { Button } from '@/components/ui/button'
import { useEffect, useRef, useSyncExternalStore } from 'react'
import { chapterStore } from './chapter-store'
import styles from './chapterCopy.module.css'
import { CHAPTER_COPY } from './motion/chapters'

// 왼쪽 아래 챕터 카피 — animejs.com 식(2026-09-14): 마스크 뒤에서 줄 단위로 솟는 제목·설명.
//   사이드바와 같은 chapterStore 를 읽어 트리거 즉시 바뀐다(2026-09-14 사용자 결정 — 모션 뒤로 늦추던 지연은 뺐다).
//   챕터가 X → Y 로 바뀌면 X 의 줄이 마스크 **위로** 다 빠져나간 **다음** Y 의 줄이 아래에서 솟는다(순서대로, 2026-09-14 사용자 결정 — 동시도 아니고 3D 모션에 묶지도 않는다).
//   나가는 중에 또 바뀌면 목표(pending)만 갈아치우고, 퇴장이 끝나는 자리에서 그 최신 목표를 들여온다.
// 줄은 문구의 \n 으로 여기서 직접 나눈다(animejs/text split 은 안 쓴다 — 라이브러리가 DOM 을 갈아끼우면 React 와 소유권이 겹친다).
// 7장을 모두 그려 두고 줄의 transform 만 움직인다(레이아웃 고정, article 자체는 늘 보이는 상태). 기본값은 마스크 아래(CSS translateY 110%) 라 아무것도 안 보인다
const RISE = { title: 720, body: 600, cta: 500 } // 등장 ms
const EXIT = { line: 420, cta: 220 } // 퇴장 ms — 등장보다 짧게, 지루하지 않게
const STAGGER = { title: 90, body: 70, exit: 45 } // 줄 사이 간격 ms
const BODY_AT = 260 // 제목이 올라오기 시작하고 이만큼 뒤에 설명 시작
const REDUCED_RATE = 0.4 // 우리 방침: reduced-motion 은 끄지 않고 빠르게(landingScene 과 같은 결)

const lines = (text: string, cls: string) =>
  text.split('\n').map((line, i) => (
    <span key={i} className={styles.line}>
      <span data-line className={cls}>
        {line}
      </span>
    </span>
  ))

type Running = { enter?: Timeline; exit?: Timeline }

export function ChapterCopy() {
  const active = useSyncExternalStore(chapterStore.subscribe, chapterStore.get, () => -1)
  const items = useRef<(HTMLElement | null)[]>([])
  const running = useRef<Running[]>([]) // 카드별로 돌고 있는 타임라인 — 같은 카드가 나가다 다시 들어올 때 서로 싸우지 않게
  const shown = useRef(-1) // 지금 들어와 있는(또는 들어오는 중인) 카드
  const pending = useRef(-1) // 최종 목표 — 퇴장이 끝났을 때 이걸 들여온다

  useEffect(() => {
    const rate = matchMedia('(prefers-reduced-motion: reduce)').matches ? REDUCED_RATE : 1
    const parts = (i: number) => {
      const art = items.current[i]
      return art && { title: art.querySelectorAll(`.${styles.title} [data-line]`), body: art.querySelectorAll(`.${styles.body} [data-line]`), cta: art.querySelector(`.${styles.cta}`) }
    }
    /** 돌던 것을 전부 걷어내 CSS 기본값(마스크 아래)으로 되돌린다 */
    const reset = (i: number) => {
      const r = running.current[i]
      r?.exit?.revert()
      r?.enter?.revert()
      running.current[i] = {}
      if (shown.current === i) shown.current = -1
    }
    const enter = (i: number) => {
      const p = parts(i)
      if (!p) return
      reset(i)
      const tl = createTimeline({ defaults: { ease: 'out(3)' } })
      tl.add(p.title, { y: ['110%', '0%'], duration: RISE.title * rate, delay: stagger(STAGGER.title * rate) }, 0)
      tl.add(p.body, { y: ['110%', '0%'], opacity: [0, 1], duration: RISE.body * rate, delay: stagger(STAGGER.body * rate) }, BODY_AT * rate)
      if (p.cta) tl.add(p.cta, { opacity: [0, 1], y: [10, 0], duration: RISE.cta * rate }, (BODY_AT + 300) * rate)
      running.current[i] = { enter: tl }
      shown.current = i
    }
    /** 지금 보이는 자리에서 그대로 위로 빠져나가고, 다 나가면 그 시점의 목표(pending)를 들여온다. 이미 나가는 중이면 그대로 둔다(목표만 바뀐 것). 시작했으면 true */
    const exit = (i: number) => {
      const p = parts(i)
      const r = running.current[i]
      if (!p || !r?.enter) return false
      if (r.exit) return true
      r.enter.pause()
      const tl = createTimeline({
        defaults: { ease: 'in(2)' },
        onComplete: () => {
          reset(i)
          if (pending.current >= 0) enter(pending.current)
        },
      })
      tl.add(p.title, { y: '-110%', duration: EXIT.line * rate, delay: stagger(STAGGER.exit * rate) }, 0)
      tl.add(p.body, { y: '-110%', opacity: 0, duration: EXIT.line * rate, delay: stagger(STAGGER.exit * rate) }, 0)
      if (p.cta) tl.add(p.cta, { opacity: 0, duration: EXIT.cta * rate }, 0)
      r.exit = tl
      return true
    }

    pending.current = active
    const leaving = shown.current
    if (leaving >= 0 && leaving !== active && exit(leaving)) return // 등장은 퇴장이 끝나는 자리에서
    if (active >= 0) enter(active)
  }, [active])

  // 언마운트 — 인라인 스타일을 전부 걷어낸다
  useEffect(
    () => () => {
      running.current.forEach((r) => {
        r?.exit?.revert()
        r?.enter?.revert()
      })
      running.current = []
    },
    [],
  )

  return (
    <div className={styles.root}>
      <div className={styles.stack}>
        {CHAPTER_COPY.map((c, i) => (
          <article
            key={c.title}
            ref={(el) => {
              items.current[i] = el
            }}
            className={`${styles.item} ${active === i ? styles.shown : ''}`}
            aria-hidden={active !== i}
          >
            <h2 className={styles.title}>{lines(c.title, styles.titleLine)}</h2>
            <p className={styles.body}>{lines(c.body, styles.bodyLine)}</p>
            {/* 마지막 챕터(07 시작하기)만 CTA — Hero 를 지우며 없어진 진입 버튼 자리(landing.md).
                목적지는 /dashboard 하나(2026-09-14 사용자 결정): 로그인 안 했으면 proxy.ts 가 /login?next=/dashboard 로 보내고 로그인 뒤 돌아온다 */}
            {i === CHAPTER_COPY.length - 1 && (
              <div className={styles.cta}>
                <Button href="/dashboard" size="lg">
                  시작하기
                </Button>
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  )
}
