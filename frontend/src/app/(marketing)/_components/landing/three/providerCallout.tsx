'use client'

import { useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { type Timeline } from 'animejs'
import { type ReactNode, useEffect, useMemo, useRef } from 'react'
import { type Object3D } from 'three'
import { chapterStore } from '../chapter-store'
import { CAM_ORBIT } from '../motion/chapters'
import { P } from '../progress'
import { type Side, type Vec3, Pointer, calloutStyles as styles, countUp, enterAll, exitAll, motionRate, partOf, pinsReady, rightOfYaw, sideFor, topOf, usePins } from './callout'
import { DRACO, MODEL } from './gpuModel'

// 04 공급자 — 히트싱크가 들리며 기판이 드러나는 동안(초크가 펌핑하고 LED 가 깜빡이는 = "일하는" 장면) 항목마다 실제 부품에 선이 붙는다(2026-09-14, animejs.com 식):
//   가동 시간 → PWM IC(전원 제어)   수익 → 16핀 전원 커넥터   온라인 → 상태 LED   30초 생존 신호 → PCB 배선(Traces)   켜면 받고 끄면 빠짐 → I/O 브라켓(커넥터 끝). 라벨은 짧게 — 작은 화면(720 높이)에서 옆 라벨·사이드바에 닿았다
// 숫자는 예시(챕터 3 의 600 GSC/시간 과 맞춘 3시간 12분 → 1,920 GSC)
const CHAPTER = 3 // 사이드바 04 = 인덱스 3
// 켜는 시점(2026-09-17 밸런싱): 히트싱크가 절반 들렸을 때(P.hsLift). 이전엔 이탈이 0.6 진행된 뒤(P.hsOff)였는데 이탈 트윈이 in(3) 가속이라
// 그 지점이 트리거 뒤 약 0.9초, 거기에 선·라벨 등장(~0.8초)이 더해져 왼쫽 카피(트리거 즉시)보다 1초 넘게 늦었다.
// hsLift 는 inOut(2) 500ms 라 0.5 는 트리거 뒤 약 0.23초 — 카피 제목이 올라오는 동안 선이 같이 그려진다. 되감아 내려오면 같은 지점에서 꺼진다
const SHOW_AT = 0.5
const GAIN = { minutes: 192, gsc: 1920, at: 300, duration: 1400 }
const LED: Vec3 = [-0.89, -0.2 + 0.012, -0.44] // statusLeds.tsx 의 heartbeat LED(BIOS IC 옆)
const CAP_O: Vec3 = [0.3, -0.1, 0.5] // tools/gpu_model.py Cap_O0 윗면(기판 -0.19 + 캡 높이)
const fmtHours = (m: number) => {
  const v = Math.round(m)
  return `${Math.floor(v / 60)}시간 ${v % 60}분`
}
type Spec = { key: string; part?: string[]; at?: Vec3; lift?: number; side?: Side; offset?: Vec3 } // side: 카메라 판정 대신 고정 — 카드 중앙 부품이 왼쪽 카피와 겹칠 때. offset: 앵커를 비켜설 때(월드 좌표)
const SPECS: Spec[] = [
  { key: 'time', part: ['PWM_IC'], side: 'right' }, // 카드 중앙 근처 — 왼쪽으로 뻗으면 카피(x 64~640)와 겹친다
  { key: 'gain', part: ['Power_16pin'] },
  { key: 'online', at: LED },
  { key: 'beat', at: CAP_O, side: 'right' }, // 출력단 커패시터(Cap_O0, 기판 아래쪽 가장자리·PWM IC 왼쪽) — 가동(PWM IC) 앵커보다 화면 왼쪽·위라 둘 다 오른쫽으로 뻗어도 선이 X 로 안 겹친다. Vias 에 두면 어느 해상도든 가동 선과 교차했고(2026-09-15), Cap_ 은 gpuModel 에서 병합돼 이름으로 못 찾는다
  { key: 'accept', part: ['IO_Bracket'] }, // 커넥터 쪽 끝 — "작업이 들어온다". 다이(GPU_Die)에 두면 LED 라벨과 겹쳤다
]

export function ProviderCallout() {
  const { scene } = useGLTF(MODEL, DRACO)
  const targets = useMemo(() => {
    const right = rightOfYaw(CAM_ORBIT.yaw) // 이 챕터 카메라(chapters.ts) — 앵커가 화면 좌/우 어디에 오는지 판정용
    return SPECS.map((s) => {
      const target: Object3D | Vec3 = s.at ?? partOf(scene, ...s.part!)
      const p = Array.isArray(target) ? target : topOf(target)
      return { target, side: s.side ?? (sideFor(p, right) as Side), lift: s.lift ?? 0, offset: s.offset }
    })
  }, [scene])
  const pins = usePins(SPECS.length)
  const nums = useRef<{ time?: HTMLElement | null; gsc?: HTMLElement | null }>({})
  const shown = useRef(false)
  const tl = useRef<Timeline | null>(null)

  useEffect(
    () => () => {
      tl.current?.pause()
    },
    [],
  )

  useFrame(() => {
    const { time, gsc } = nums.current
    const on = chapterStore.get() === CHAPTER && P.hsLift >= SHOW_AT
    if (on === shown.current || !pinsReady(pins) || !time || !gsc) return
    shown.current = on
    const rate = motionRate()
    tl.current?.pause()
    if (on) {
      const t = enterAll(pins, rate)
      countUp(t, time, 0, GAIN.minutes, { at: GAIN.at, duration: GAIN.duration, rate, format: fmtHours })
      countUp(t, gsc, 0, GAIN.gsc, { at: GAIN.at, duration: GAIN.duration, rate, format: (v) => '+' + Math.round(v).toLocaleString('ko-KR') })
      tl.current = t
    } else tl.current = exitAll(pins, rate)
  })

  const pin = (i: number, body: ReactNode, extra = '') => (
    <Pointer key={SPECS[i].key} pin={pins[i]} target={targets[i].target} lift={targets[i].lift} offset={targets[i].offset} side={targets[i].side}>
      <li className={`${styles.item} ${extra}`}>
        <span className={styles.tick} />
        {body}
      </li>
    </Pointer>
  )

  return (
    <>
      {pin(
        0,
        <>
          가동{' '}
          <b
            ref={(el) => {
              nums.current.time = el
            }}
          >
            0시간 0분
          </b>
        </>,
      )}
      {pin(
        1,
        <>
          수익{' '}
          <b
            className={styles.gain}
            ref={(el) => {
              nums.current.gsc = el
            }}
          >
            +0
          </b>{' '}
          GSC
        </>,
      )}
      {pin(2, '온라인', styles.live)}
      {pin(3, '30초마다 생존 신호', styles.dim)}
      {pin(4, '켜면 받고, 끄면 빠짐', styles.dim)}
    </>
  )
}
