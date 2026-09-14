'use client'

import { useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { type Timeline } from 'animejs'
import { useEffect, useMemo, useRef } from 'react'
import { chapterStore } from '../chapter-store'
import { CAM0, CAM_ORBIT } from '../motion/chapters'
import { type Vec3, Pointer, boxOf, calloutStyles as styles, countUp, enterAll, exitAll, motionRate, onTop, pinsReady, rightOfYaw, sideFor, usePins } from './callout'
import { isFin } from './finCallout'
import { DRACO, MODEL } from './gpuModel'

// 03 사용자 — 카메라가 카드를 도는 동안(2026-09-14 사용자 결정, animejs.com 식 항목별 선):
//   −Z 긴 변 세 지점: GPU 등급 적합도 한 줄씩(4090 여유 / 4070 적합·추천 / 3060 빡빡). 추천 옆에 예상 시간·비용이 0 에서 카운트업 — 위저드 4단계가 실제로 보여주는 것
//   +Z 긴 변 가운데:  "AI가 추천해주는 최적의 GPU"
// 앵커를 카드의 두 긴 변에 둔 이유: 오빗(yaw 1.0 → π−1.0)이 +X 끝을 중심으로 좌우로 흔드는 움직임이라 −Z 변은 내내 화면 오른쪽, +Z 변은 내내 왼쪽에 머문다 →
// 선이 카드를 가로지르지 않는다. 숫자(56분·600 GSC)는 job.md 의 목업 견적(AUTO 3 epochs) — 예시
const CHAPTER = 2 // 사이드바 03 = 인덱스 2
const EDGE = 0.1 // 긴 변에서 안쪽으로 이 비율 지점(0 = 모서리)
const COUNT = { min: 56, gsc: 600, at: 560, duration: 900 } // 추천 핀이 뜰 즈음 시작
const FITS: { gpu: string; fit: string; tone: 'good' | 'ok' | 'tight'; rec?: boolean; fx: number }[] = [
  { gpu: 'RTX 4090', fit: '여유', tone: 'good', fx: 0.2 },
  { gpu: 'RTX 4070', fit: '적합 · 추천', tone: 'ok', rec: true, fx: 0.5 },
  { gpu: 'RTX 3060', fit: '빡빡', tone: 'tight', fx: 0.8 },
]
const AI = 'AI가 추천해주는 최적의 GPU'

export function GpuFitCallout() {
  const { scene } = useGLTF(MODEL, DRACO)
  const spots = useMemo(() => {
    const box = boxOf(scene, isFin)
    const right = rightOfYaw((CAM0.yaw + CAM_ORBIT.yaw) / 2) // 오빗 중간값 — 좌/우 판정은 z 부호로만 갈린다(chapters.ts 카메라가 바뀌면 함께 따라간다)
    const fits = FITS.map((f) => {
      const at: Vec3 = onTop(box, f.fx, EDGE) // −Z 변 쪽
      return { ...f, at, side: sideFor(at, right) }
    })
    const ai: Vec3 = onTop(box, 0.5, 1 - EDGE) // +Z 변 가운데
    return { fits, ai, aiSide: sideFor(ai, right) }
  }, [scene])
  const pins = usePins(FITS.length + 1) // 마지막이 AI
  const nums = useRef<{ min?: HTMLElement | null; gsc?: HTMLElement | null }>({})
  const shown = useRef(false)
  const tl = useRef<Timeline | null>(null)

  useEffect(
    () => () => {
      tl.current?.pause()
    },
    [],
  )

  useFrame(() => {
    const { min: minEl, gsc: gscEl } = nums.current
    const on = chapterStore.get() === CHAPTER
    if (on === shown.current || !pinsReady(pins) || !minEl || !gscEl) return
    shown.current = on
    const rate = motionRate()
    tl.current?.pause()
    if (on) {
      const t = enterAll(pins, rate)
      countUp(t, minEl, 0, COUNT.min, { at: COUNT.at, duration: COUNT.duration, rate })
      countUp(t, gscEl, 0, COUNT.gsc, { at: COUNT.at, duration: COUNT.duration, rate })
      tl.current = t
    } else tl.current = exitAll(pins, rate)
  })

  return (
    <>
      {spots.fits.map((f, i) => (
        <Pointer key={f.gpu} pin={pins[i]} target={f.at} side={f.side}>
          <li className={`${styles.item} ${f.rec ? styles.stack : styles.dim}`}>
            <span className={styles.tick} />
            {f.gpu}
            <em className={styles.fit} data-tone={f.tone}>
              {f.fit}
            </em>
            {f.rec && (
              <span className={styles.est}>
                <b
                  ref={(el) => {
                    nums.current.min = el
                  }}
                >
                  0
                </b>
                분 ·{' '}
                <b
                  ref={(el) => {
                    nums.current.gsc = el
                  }}
                >
                  0
                </b>{' '}
                GSC
              </span>
            )}
          </li>
        </Pointer>
      ))}
      <Pointer pin={pins[FITS.length]} target={spots.ai} side={spots.aiSide}>
        <li className={styles.item}>
          <span className={styles.tick} />
          {AI}
        </li>
      </Pointer>
    </>
  )
}
