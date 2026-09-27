'use client'

import { Html, useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { createTimeline, type Timeline } from 'animejs'
import { useEffect, useMemo, useRef } from 'react'
import { Vector3 } from 'three'
import { chapterStore } from '../chapter-store'
import { CalloutBody, type Vec3, addEnter, addExit, boxOf, calloutReady, calloutStyles as styles, countUp, hideCallout, motionRate, useCalloutNodes } from './callout'
import { DRACO, MODEL } from './gpuModel'

// 05 에스크로 — 탑뷰, 카드는 정지. 정지 화면이라 "숫자가 옮겨가는 장부" 로 카피를 보여준다(2026-09-14):
//   카드 중앙(콜드플레이트): 의뢰자 / 에스크로 / 공급자 세 줄. '작업 의뢰' → 600 이 에스크로로, '작업 완료' → 600 이 공급자로
//   장부 순환은 챕터에 머무는 동안 **계속 반복**된다(2026-09-15 사용자 요청) — 등장(점·선·항목)은 한 번, 순환만 loop 타임라인으로 따로 돈다
//   (취소·환불 콜아웃은 넣었다가 사용자 요청으로 뺐다 — 카피 마지막 줄이 이미 말한다)
// 금액 600 GSC 는 챕터 3 견적과 같은 예시. 컨트랙트는 실제로 예치액을 전액 지급하거나 전액 환불한다(부분 정산 없음) — 이 장부가 그 구조 그대로다
const CHAPTER = 4 // 사이드바 05 = 인덱스 4
const PLATE_LIFT = 0.2 // 챕터 4 에서 콜드플레이트가 들려 있는 높이(gpuModel PLATE_LIFT) — 앵커도 그만큼 위
const AMOUNT = 600
const START_AT = 700 // 등장 뒤 순환 시작(ms, rate 반영 전)
// 순환 한 바퀴(ms, rate 반영 전) — 2026-09-15 사용자 스토리보드:
//   ① 의뢰자 600 · 에스크로 0 · 공급자 0 으로 시작해 잠깐 머문다
//   ② '작업 의뢰' 가 뜨고 → 600 이 의뢰자에서 에스크로로 옮겨간다 → 머문다
//   ③ '작업 완료' 가 뜨고 → 600 이 에스크로에서 공급자로 옮겨간다 → 머문다(loopDelay) → ①
const C = { start: 1000, request: 1000, deposit: 1300, complete: 3400, payout: 3600, move: 900, hold: 1300 }

export function EscrowCallout() {
  const { scene } = useGLTF(MODEL, DRACO)
  const layout = useMemo(() => {
    const plate = boxOf(scene, (n) => n === 'ColdPlate')
    const c = plate.getCenter(new Vector3())
    const anchor: Vec3 = [c.x, plate.max.y + PLATE_LIFT, c.z]
    return { anchor, side: 'right' as const } // 콜드플레이트는 카드 중앙 — 카메라 판정(x≈−0.25 → 왼쪽)대로 두면 왼쪽 카피와 겹친다. 중앙에서 오른쪽으로 나가는 선은 카드를 가로지르지 않는다
  }, [scene])

  const nodes = useCalloutNodes()
  const cells = useRef<{ client?: HTMLElement | null; escrow?: HTMLElement | null; provider?: HTMLElement | null; badge?: HTMLElement | null }>({})
  const shown = useRef(false)
  const tl = useRef<Timeline | null>(null) // 등장·퇴장
  const cycle = useRef<Timeline | null>(null) // 장부 순환(loop)

  useEffect(
    () => () => {
      tl.current?.pause()
      cycle.current?.pause()
    },
    [],
  )

  useFrame(() => {
    const n = nodes.current
    const { client, escrow, provider, badge } = cells.current
    const on = chapterStore.get() === CHAPTER
    if (on === shown.current || !calloutReady(n) || !client || !escrow || !provider || !badge) return
    shown.current = on
    const rate = motionRate()
    tl.current?.pause()
    cycle.current?.pause()
    if (on) {
      // 순환 한 바퀴 — loop 라 매 바퀴 tween 은 from 값(600/0/0)으로 되돌아가고, call 은 매 바퀴 다시 불린다
      // ① 세 칸을 출발값으로. anime 은 tween 이 '시작될 때' 값을 되돌리므로 아직 안 시작한 칸은 지난 바퀴 값이 남는다 → 바퀴마다 직접 되돌린다.
      //    countUp 이 만들어지는 순간에도 자기 from 값을 써 버리므로(에스크로 600→0 tween 이 '600' 을 씀) 타임라인을 다 만든 뒤 한 번 더 부른다
      const reset = () => {
        client.textContent = String(AMOUNT)
        escrow.textContent = '0'
        provider.textContent = '0'
        badge.textContent = '' // 비어 있으면 CSS(.fit:empty) 가 접는다 — hidden 속성은 쓰지 않는다
      }
      const c = createTimeline({ loop: true, loopDelay: C.hold * rate, autoplay: false })
      c.call(reset, 0)
      c.call(() => {
        badge.dataset.tone = 'tight'
        badge.textContent = '작업 의뢰' // ②
      }, C.request * rate)
      countUp(c, client, AMOUNT, 0, { at: C.deposit, duration: C.move, rate }) // 의뢰자 → 에스크로
      countUp(c, escrow, 0, AMOUNT, { at: C.deposit, duration: C.move, rate })
      c.call(() => {
        badge.dataset.tone = 'ok'
        badge.textContent = '작업 완료' // ③
      }, C.complete * rate)
      countUp(c, escrow, AMOUNT, 0, { at: C.payout, duration: C.move, rate }) // 에스크로 → 공급자
      countUp(c, provider, 0, AMOUNT, { at: C.payout, duration: C.move, rate })
      reset() // 등장하는 동안(순환 시작 전) 장부가 ① 상태로 보이게
      cycle.current = c
      // 등장은 한 번 — 항목이 다 뜬 뒤 순환을 켠다
      const t = createTimeline()
      addEnter(t, n, rate, 0)
      t.call(() => c.play(), START_AT * rate)
      tl.current = t
    } else {
      const t = createTimeline({ onComplete: () => hideCallout(n) })
      addExit(t, n, rate, 0)
      tl.current = t
    }
  })

  const cell = (key: 'client' | 'escrow' | 'provider', who: string, initial: number) => (
    <li className={styles.item}>
      <span className={styles.tick} />
      <span className={styles.who}>{who}</span>
      <b
        className={styles.amt}
        ref={(el) => {
          cells.current[key] = el
        }}
      >
        {initial}
      </b>
      {/* 배지의 내용·data-tone 은 React 가 아니라 위 타임라인(t.call)만 쓴다 — JSX 에 hidden/data-tone 을 두면 React 가 다시 그릴 때 되돌릴 수 있어(DOM 소유권 충돌) 처음부터 비워 둔다.
          같은 이유로 이 컴포넌트는 state 를 갖지 않는다(마운트 뒤 리렌더 없음) */}
      {key === 'escrow' && (
        <em
          className={styles.fit}
          ref={(el) => {
            cells.current.badge = el
          }}
        />
      )}
    </li>
  )

  return (
    <Html position={layout.anchor} zIndexRange={[20, 20]} style={{ pointerEvents: 'none' }}>
      <CalloutBody nodes={nodes} side={layout.side} caption="에스크로 · GSC">
        {cell('client', '의뢰자', AMOUNT)}
        {cell('escrow', '에스크로', 0)}
        {cell('provider', '공급자', 0)}
      </CalloutBody>
    </Html>
  )
}
