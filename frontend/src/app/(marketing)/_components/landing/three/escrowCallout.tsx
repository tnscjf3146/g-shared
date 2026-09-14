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
//   카드 중앙(콜드플레이트): 의뢰자 / 에스크로 / 공급자 세 줄. 1단 예치(의뢰자 → 에스크로, '잠김') → 2단 완료(에스크로 → 공급자, '지급 완료')
//   (취소·환불 콜아웃은 넣었다가 사용자 요청으로 뺐다 — 카피 마지막 줄이 이미 말한다)
// 금액 600 GSC 는 챕터 3 견적과 같은 예시. 컨트랙트는 실제로 예치액을 전액 지급하거나 전액 환불한다(부분 정산 없음) — 이 장부가 그 구조 그대로다
const CHAPTER = 4 // 사이드바 05 = 인덱스 4
const PLATE_LIFT = 0.2 // 챕터 4 에서 콜드플레이트가 들려 있는 높이(gpuModel PLATE_LIFT) — 앵커도 그만큼 위
const AMOUNT = 600
const T = { deposit: 700, lock: 1650, payout: 2600, done: 3550, move: 900 } // ms(rate 반영 전)

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
  const tl = useRef<Timeline | null>(null)

  useEffect(
    () => () => {
      tl.current?.pause()
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
    if (on) {
      badge.hidden = true
      const t = createTimeline()
      addEnter(t, n, rate, 0)
      // 1단 예치: 의뢰자 → 에스크로
      countUp(t, client, AMOUNT, 0, { at: T.deposit, duration: T.move, rate })
      countUp(t, escrow, 0, AMOUNT, { at: T.deposit, duration: T.move, rate })
      t.call(() => {
        badge.hidden = false
        badge.dataset.tone = 'tight'
        badge.textContent = '잠김'
      }, T.lock * rate)
      // 2단 완료: 에스크로 → 공급자
      countUp(t, escrow, AMOUNT, 0, { at: T.payout, duration: T.move, rate })
      countUp(t, provider, 0, AMOUNT, { at: T.payout, duration: T.move, rate })
      t.call(() => {
        badge.dataset.tone = 'ok'
        badge.textContent = '지급 완료'
      }, T.done * rate)
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
      {key === 'escrow' && (
        <em
          className={styles.fit}
          data-tone="tight"
          hidden
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
