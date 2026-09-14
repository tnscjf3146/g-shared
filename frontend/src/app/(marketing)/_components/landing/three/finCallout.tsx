'use client'

import { useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { type Timeline } from 'animejs'
import { useEffect, useMemo, useRef } from 'react'
import { chapterStore } from '../chapter-store'
import { CAM0 } from '../motion/chapters'
import { P } from '../progress'
import { Pointer, boxOf, calloutStyles as styles, enterAll, exitAll, motionRate, onTop, pinsReady, rightOfYaw, sideFor, usePins } from './callout'
import { DRACO, MODEL } from './gpuModel'

// 02 잠든카드 — 팬·쉬라우드가 올라가 핀 뭉치가 드러나면, 핀 위 네 지점에서 선이 하나씩 나와 "이 카드로 할 수 있는 일" 이 항목별로 붙는다(2026-09-14, animejs.com 식).
// 보임/숨김은 useFrame 에서 P.shroud 와 현재 챕터를 읽어 결정(React state 없음) — 뒤집히는 순간에만 anime 타임라인을 돌린다. 그리기·움직임은 callout.tsx 공용
const CHAPTER = 1 // 사이드바 02 = 인덱스 1
const SHOW_AT = 0.55 // 쉬라우드가 이만큼 올라가면(핀이 보이기 시작) 켠다. 되감아 내려오면 같은 지점에서 꺼진다
/** GPU 로 돌릴 수 있는 AI 작업 — 카탈로그의 workload 세 종류(학습·추론·평가)를 사람 말로. 핀 뭉치 길이 방향 fx 지점에 하나씩 */
const TASKS: { label: string; fx: number }[] = [
  { label: 'LLM 미세조정 (LoRA)', fx: 0.15 },
  { label: '텍스트 분류 · 임베딩 학습', fx: 0.38 },
  { label: '대량 배치 추론', fx: 0.62 },
  { label: '모델 성능 평가', fx: 0.85 },
]

export const isFin = (name: string) => name.startsWith('Fin_') // 병합 전(Fin_1…)·후(Fin_merged) 모두

export function FinCallout() {
  const { scene } = useGLTF(MODEL, DRACO)
  const spots = useMemo(() => {
    const box = boxOf(scene, isFin)
    const right = rightOfYaw(CAM0.yaw) // 이 챕터 카메라(chapters.ts) — 앵커가 화면 좌/우 어디에 오는지 판정용
    return TASKS.map((t) => {
      const at = onTop(box, t.fx)
      return { ...t, at, side: sideFor(at, right) }
    })
  }, [scene])
  const pins = usePins(TASKS.length)
  const shown = useRef(false)
  const tl = useRef<Timeline | null>(null)

  useEffect(
    () => () => {
      tl.current?.pause()
    },
    [],
  )

  useFrame(() => {
    const on = chapterStore.get() === CHAPTER && P.shroud >= SHOW_AT
    if (on === shown.current || !pinsReady(pins)) return
    shown.current = on
    tl.current?.pause() // 반대 방향 타임라인이 돌고 있으면 그 자리에서 멈추고 이어받는다(되돌리지 않는다 — 되돌리면 순간 이동)
    tl.current = on ? enterAll(pins, motionRate()) : exitAll(pins, motionRate())
  })

  return (
    <>
      {spots.map((s, i) => (
        <Pointer key={s.label} pin={pins[i]} target={s.at} side={s.side}>
          <li className={styles.item}>
            <span className={styles.tick} />
            {s.label}
          </li>
        </Pointer>
      ))}
    </>
  )
}
