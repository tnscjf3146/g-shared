'use client'

import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { createTimeline, stagger, type Timeline } from 'animejs'
import { type ReactNode, type RefObject, useEffect, useMemo, useRef } from 'react'
import { Box3, type Group, type Object3D, Vector3 } from 'three'
import styles from './callout.module.css'

// 3D 위 지시선 콜아웃의 공통 부품(2026-09-14) — 02 핀(finCallout) · 03 GPU 등급(gpuFitCallout) · 04 공급자(providerCallout) · 05 에스크로(escrowCallout) · 06 블록체인(chainCallout).
// animejs.com 식 주석: **항목 하나 = 선 하나 = 부품 하나**(사용자 결정). 그리는 것(CalloutBody·Pointer)과 움직이는 것(addEnter/addExit/enterAll/exitAll/countUp/scrambleIn)을
// 여기 두고, 언제 켜고 끌지(트리거)와 어느 부품에 붙일지는 각 챕터 컴포넌트가 정한다.
//
// 선 그리기는 SVG 기본 기능으로: polyline 에 pathLength=1 을 줘 길이를 1 로 정규화하고 stroke-dasharray 1 위에서 dashoffset 만 움직인다.
//   등장  offset 1 → 0   : 보이는 구간이 [0, 1-o] 로 자란다 = 앵커에서부터 그려진다
//   퇴장  offset 0 → -1  : 보이는 구간이 [-o, 1] 로 줄어든다 = 앵커에서부터 지워진다
// animejs/svg 의 createDrawable 은 안 쓴다 — <Html> 이 자기 React 루트로 자식을 다시 그리면 마운트 때 잡아 둔 프록시가 죽은 노드를 가리켰다
export const L1 = 44 // 지시선: 대각(↗ 또는 ↖) 길이 px
export const L2 = 36 // 지시선: 수평 길이 px
export const GAP = 12 // 선 끝 → 패널
export const REDUCED_RATE = 0.4 // 우리 방침: reduced-motion 은 끄지 않고 빠르게(landingScene 과 같은 결)
export const STEP = 110 // 한 챕터 안에서 핀들이 차례로 나오는 간격 ms

export type Vec3 = [number, number, number]
export type Side = 'right' | 'left'
type Scene = { traverse: (cb: (o: Object3D) => void) => void; getObjectByName: (n: string) => Object3D | undefined }

/** 이름 조건에 맞는 부품들의 바운딩박스(월드). 모델은 원점에 identity 로 놓이므로 glTF 좌표 = 월드 좌표 */
export function boxOf(scene: Scene, test: (name: string) => boolean) {
  const box = new Box3()
  scene.traverse((o) => {
    if (test(o.name)) box.union(new Box3().setFromObject(o))
  })
  return box
}
/** 이름으로 부품 하나. 병합된 그룹(Fin_merged 등)은 병합 이름 → 접두 순으로 찾는다. 없으면 scene 원점(눈에 띄게 어긋나므로 이름 오타를 바로 알 수 있다) */
export function partOf(scene: Scene, ...names: string[]): Object3D {
  for (const n of names) {
    const o = scene.getObjectByName(n)
    if (o) return o
  }
  let hit: Object3D | undefined
  scene.traverse((o) => {
    if (!hit && names.some((n) => o.name.startsWith(n))) hit = o
  })
  return hit ?? (scene as unknown as Object3D)
}
/** 부품 상자의 윗면 중심(월드) — 지금 위치 기준 */
export const topOf = (o: Object3D): Vec3 => {
  const b = new Box3().setFromObject(o)
  const c = b.getCenter(new Vector3())
  return [c.x, b.max.y, c.z]
}
/** 상자의 윗면을 x·z 비율(0..1)로 짚은 점 — 핀 뭉치 위 여러 지점처럼 "부품 하나에 여러 항목" 일 때 */
export const onTop = (box: Box3, fx: number, fz = 0.5): Vec3 => [box.min.x + (box.max.x - box.min.x) * fx, box.max.y, box.min.z + (box.max.z - box.min.z) * fz]
/**
 * 앵커가 화면의 왼쪽/오른쪽 어디에 오는지 — 그 챕터 카메라의 "화면 오른쪽" 벡터(xz 평면)와 내적한다.
 * 카메라 yaw θ 에서 화면 오른쪽 ≈ (cos θ, 0, −sin θ). 패널은 앵커 바깥쪽으로 뻗어야 선이 카드를 가로지르지 않는다
 */
export const sideFor = (a: Vec3, right: [number, number]): Side => (a[0] * right[0] + a[2] * right[1] >= 0 ? 'right' : 'left')
export const rightOfYaw = (yaw: number): [number, number] => [Math.cos(yaw), -Math.sin(yaw)]

/** 콜아웃 한 개의 DOM 노드들. CalloutBody 가 콜백 ref 로 채우고, 챕터 컴포넌트가 useFrame 안에서 읽는다(렌더 중엔 안 읽는다 — react-hooks/refs) */
export type CalloutNodes = { root?: HTMLDivElement | null; dot?: SVGCircleElement | null; line?: SVGPolylineElement | null; caption?: HTMLSpanElement | null; list?: HTMLUListElement | null }
export type Pin = { current: CalloutNodes }
export const useCalloutNodes = () => useRef<CalloutNodes>({})
/** 핀 n 개의 노드 묶음. useRef 가 아니라 useMemo 로 만든 평범한 객체라 렌더 중에 pins[i] 를 넘겨도 된다(react-hooks/refs 가 ref 로 안 본다) */
export const usePins = (n: number): Pin[] => useMemo(() => Array.from({ length: n }, () => ({ current: {} as CalloutNodes })), [n])
/** <Html> 이 자식을 자기 루트로 그리므로 첫 프레임엔 노드가 비어 있을 수 있다 — 전부 붙은 뒤에만 움직인다 */
export const calloutReady = (n: CalloutNodes): n is Required<CalloutNodes> => !!(n.root && n.dot && n.line && n.caption && n.list)
export const pinsReady = (pins: Pin[]) => pins.every((p) => calloutReady(p.current))

export const motionRate = () => (matchMedia('(prefers-reduced-motion: reduce)').matches ? REDUCED_RATE : 1)

type Opts = { items?: boolean } // items:false — 항목은 호출측이 따로 움직인다
/** 등장: 점 → 선이 앵커에서부터 그려지고 → 캡션 → 항목. from 값을 명시해 어느 상태에서 시작해도 같다. at = 타임라인 위 시작 위치(ms, rate 반영 전) */
export function addEnter(t: Timeline, n: Required<CalloutNodes>, rate: number, at = 0, opts: Opts = {}) {
  n.root!.classList.add(styles.on)
  t.add(n.dot!, { opacity: [0, 1], scale: [0, 1], duration: 220 * rate, ease: 'out(3)' }, at * rate)
  t.add(n.line!, { strokeDashoffset: [1, 0], duration: 380 * rate, ease: 'inOut(2)' }, (at + 80) * rate)
  t.add(n.caption!, { opacity: [0, 1], x: [-8, 0], duration: 360 * rate, ease: 'out(3)' }, (at + 300) * rate)
  if (opts.items !== false) t.add(n.list!.children, { opacity: [0, 1], x: [-10, 0], duration: 420 * rate, delay: stagger(60 * rate), ease: 'out(3)' }, (at + 380) * rate)
}
/** 퇴장: 점이 꺼지고, 선이 앵커에서부터 지워져 패널 쪽으로 물러나고, 그 끝에 닿을 즈음 캡션·항목이 빠진다. visibility 는 호출측이 onComplete 에서 내린다 */
export function addExit(t: Timeline, n: Required<CalloutNodes>, rate: number, at = 0, opts: Opts = {}) {
  t.add(n.dot!, { opacity: [1, 0], scale: [1, 0], duration: 160 * rate, ease: 'in(2)' }, at * rate)
  t.add(n.line!, { strokeDashoffset: [0, -1], duration: 300 * rate, ease: 'inOut(2)' }, (at + 40) * rate)
  t.add(n.caption!, { opacity: 0, x: 6, duration: 160 * rate, ease: 'in(2)' }, (at + 180) * rate)
  if (opts.items !== false) t.add(n.list!.children, { opacity: 0, x: 6, duration: 160 * rate, delay: stagger(25 * rate, { from: 'last' }), ease: 'in(2)' }, (at + 200) * rate)
}
export const hideCallout = (n: CalloutNodes) => n.root?.classList.remove(styles.on)

/** 핀 여러 개가 차례로(STEP 간격) 켜지는 타임라인. 돌려주는 t 에 카운트업 등을 더 얹을 수 있다 */
export function enterAll(pins: Pin[], rate: number, at = 0, step = STEP) {
  const t = createTimeline()
  pins.forEach((p, i) => {
    if (calloutReady(p.current)) addEnter(t, p.current, rate, at + i * step)
  })
  return t
}
/** 역순으로(마지막 핀부터) 꺼지는 타임라인. 다 꺼지면 visibility 를 내린다 */
export function exitAll(pins: Pin[], rate: number, step = 60) {
  const t = createTimeline({ onComplete: () => pins.forEach((p) => hideCallout(p.current)) })
  pins.forEach((p, i) => {
    if (calloutReady(p.current)) addExit(t, p.current, rate, (pins.length - 1 - i) * step)
  })
  return t
}

type CountOpts = { at: number; duration: number; rate: number; format?: (v: number) => string }
const ko = (v: number) => Math.round(v).toLocaleString('ko-KR')
/** 숫자 카운트업 — 숫자 객체를 트윈하고 매 프레임 글자로 쓴다(DOM 속성을 직접 트윈하지 않는다: 천 단위 쉼표·단위를 우리가 찍는다) */
export function countUp(t: Timeline, el: HTMLElement, from: number, to: number, o: CountOpts) {
  const fmt = o.format ?? ko
  const n = { v: from }
  const write = () => {
    el.textContent = fmt(n.v)
  }
  write()
  t.add(n, { v: to, duration: o.duration * o.rate, ease: 'out(3)', onUpdate: write }, o.at * o.rate)
}
const HEX = '0123456789abcdef'
/** 해시가 자리 잡는 연출 — 왼쪽부터 확정되고 나머지 16진수 자리는 매 프레임 뒤섞인다(0x·… 같은 비16진 글자는 그대로). 직접 쓴 여섯 줄이라 <Html> 재렌더에도 안전 */
export function scrambleIn(t: Timeline, el: HTMLElement, text: string, o: { at: number; duration: number; rate: number }) {
  const n = { p: 0 }
  const write = () => {
    const k = Math.floor(n.p * text.length)
    let s = ''
    for (let i = 0; i < text.length; i++) s += i < k || !/[0-9a-f]/i.test(text[i]) ? text[i] : HEX[(Math.random() * 16) | 0]
    el.textContent = s
  }
  write()
  t.add(n, { p: 1, duration: o.duration * o.rate, ease: 'linear', onUpdate: write }, o.at * o.rate)
}

type BodyProps = {
  nodes: RefObject<CalloutNodes> | Pin
  /** 선이 뻗는 방향. right = ↗ 뒤 →, left = ↖ 뒤 ← (패널은 선 끝 바깥쪽) */
  side?: Side
  caption?: string // 없으면 빈 span(CSS 로 접힘) — 항목 하나짜리 핀은 캡션이 없다
  children: ReactNode // <li className={styles.item}> 들
}
/** 원점(0,0) = 앵커. 선은 대각 뒤 수평. svg 는 overflow visible 이라 음수 좌표를 그대로 쓴다 */
export function CalloutBody({ nodes, side = 'right', caption, children }: BodyProps) {
  const sx = side === 'right' ? 1 : -1
  return (
    <div
      ref={(el) => {
        nodes.current.root = el
      }}
      className={`${styles.root} ${side === 'left' ? styles.left : ''}`}
      aria-hidden
    >
      <svg className={styles.svg} width={L1 + L2} height={L1} overflow="visible" aria-hidden>
        <polyline
          ref={(el) => {
            nodes.current.line = el
          }}
          className={styles.line}
          pathLength={1}
          points={`0,0 ${sx * L1},${-L1} ${sx * (L1 + L2)},${-L1}`}
        />
        <circle
          ref={(el) => {
            nodes.current.dot = el
          }}
          className={styles.dot}
          r="3.5"
          cx="0"
          cy="0"
        />
      </svg>
      <div className={styles.panel} style={side === 'right' ? { left: L1 + L2 + GAP, top: -L1 } : { right: L1 + L2 + GAP, top: -L1 }}>
        <span
          ref={(el) => {
            nodes.current.caption = el
          }}
          className={styles.caption}
        >
          {caption ?? ''}
        </span>
        <ul
          ref={(el) => {
            nodes.current.list = el
          }}
          className={styles.list}
        >
          {children}
        </ul>
      </div>
    </div>
  )
}

type PointerProps = {
  pin: Pin
  /** 붙을 곳 — 부품(매 프레임 월드 위치를 따라간다, 06 처럼 움직이는 층) 또는 고정 좌표 */
  target: Object3D | Vec3
  lift?: number // 윗면에서 이만큼 위
  offset?: Vec3 // 윗면 중심에서 이만큼 비켜서(glTF 단위) — 같은 부품에 핀이 몰리거나 옆 라벨과 부딧힐 때
  side: Side
  caption?: string
  children: ReactNode
}
/** 부품 하나를 가리키는 핀. 부품이면 상자 윗면 중심을 원점에서의 오프셋으로 한 번 재 두고, 매 프레임 월드 위치 + 오프셋으로 그룹을 옮긴다 */
export function Pointer({ pin, target, lift = 0, offset, side, caption, children }: PointerProps) {
  const group = useRef<Group>(null)
  const [ox, oy, oz] = offset ?? [0, 0, 0]
  const off = useMemo(() => {
    if (Array.isArray(target)) return null
    const box = new Box3().setFromObject(target)
    const c = box.getCenter(new Vector3())
    const o = target.getWorldPosition(new Vector3())
    return new Vector3(c.x - o.x + ox, box.max.y - o.y + lift + oy, c.z - o.z + oz) // 상자·원점이 같은 변위를 받으므로 차이는 부품 고유 형상값
  }, [target, lift, ox, oy, oz])
  const isStatic = Array.isArray(target)
  // 고정 좌표는 한 번만 놓는다. 부품을 따라가는 핀만 매 프레임 월드 위치를 읽는다(06 의 층처럼 움직이는 것)
  useEffect(() => {
    if (isStatic && group.current) group.current.position.set(target[0] + ox, target[1] + lift + oy, target[2] + oz)
  }, [isStatic, target, ox, oy, oz, lift])
  useFrame(() => {
    const g = group.current
    if (!g || isStatic) return
    ;(target as Object3D).getWorldPosition(g.position).add(off!)
  })
  return (
    <group ref={group}>
      <Html zIndexRange={[20, 20]} style={{ pointerEvents: 'none' }}>
        <CalloutBody nodes={pin} side={side} caption={caption}>
          {children}
        </CalloutBody>
      </Html>
    </group>
  )
}
export { styles as calloutStyles }
