'use client'

import { useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { createTimeline, type Timeline } from 'animejs'
import { useEffect, useMemo, useRef } from 'react'
import { Vector3 } from 'three'
import { chapterStore } from '../chapter-store'
import { CAM_STACK } from '../motion/chapters'
import { P } from '../progress'
import { type Side, type Vec3, Pointer, addEnter, addExit, boxOf, calloutReady, calloutStyles as styles, countUp, enterAll, exitAll, hideCallout, motionRate, partOf, pinsReady, rightOfYaw, scrambleIn, sideFor, topOf, usePins } from './callout'
import { DRACO, MODEL } from './gpuModel'

// 06 블록체인 — 부품이 층층이 벌어지는 구조도. **층 = 블록, 한 층에 기록 하나**(2026-09-14, animejs.com 식):
//   백플레이트 → 참조값 · PCB → 금액 · 콜드플레이트 → 의뢰자·공급자 지갑 · 핀 층 → 상태 · 쉬라우드(맨 위) → 결과 해시
//   각 핀은 그 층의 월드 위치를 매 프레임 따라가고(Pointer), 층이 자리 잡는 P.stack 지점에서 켜진다. 되감으면 같은 지점에서 꺼진다
//   기판 +X 끝 핀 하나: tx 해시(왼쪽부터 자리 잡는 스크램블) · 블록 번호 카운트업 — "지울 수도, 바꿀 수도 없다" 를 해시가 고정되는 순간으로
// 실제 온체인 필드(externalRef · price · client/provider · state · resultHash). 해시는 목업 resultHash, 블록 번호는 GCoin 배포 블록 — 둘 다 예시
const CHAPTER = 5 // 사이드바 06 = 인덱스 5
const SHOW_AT = 0.02 // 층이 벌어지기 시작하면(P.stack) tx 핀부터
const LAYERS: { label: string; part: string[]; revealAt: number; lift?: number; side?: Side; offset?: Vec3 }[] = [
  { label: '참조값', part: ['Backplate'], revealAt: 0.12, side: 'left', offset: [-0.5, 0, 0] }, // 백플레이트·PCB 는 스택에서도 붙어 있어 같은 자리 — 금액(오른쪽)과 반대로, 앵커도 −X 로 비켜 두 선이 X 자로 교차하지 않게(2026-09-15)
  { label: '금액', part: ['PCB'], revealAt: 0.3, side: 'right', offset: [0.5, 0, 0] }, // 오른쪽으로 뻗는다(왼쪽은 카피). 앵커를 +X 로 옮겨 지갑(왼쪽으로 뻗음) 점의 오른쪽에서 출발 — 두 선이 X 자로 교차하지 않게(2026-09-15)
  { label: '의뢰자 · 공급자 지갑', part: ['ColdPlate'], revealAt: 0.5, side: 'left', offset: [0.45, 0, 0] }, // 오른쪽으로 두면 tx 지시선이 이 라벨을 가로질렀다(2026-09-15) → 왼쪽으로. 앵커를 +X 로 조금 옮겨 라벨이 왼쪽 카피(x≤~520)에 닿지 않게
  { label: '상태', part: ['Fin_merged', 'Fin_'], revealAt: 0.7 },
  { label: '결과 해시', part: ['Shroud'], revealAt: 0.9 },
]
const TX_AT = 200
const HASH = '0x9f2c…f7a8' // 앞 4·뒤 4 — 20자로 두면 720 높이 화면에서 오른쪽 챕터 사이드바에 닿았다
const BLOCK = { from: 11_212_000, to: 11_212_435, at: 700, duration: 1100 } // 첫 등장: 블록 번호 카운트업
// 그 뒤 챕터에 머무는 동안 살아 있는 체인처럼(2026-09-15 사용자 요청): 바퀴마다 블록 +1, 새 해시가 왼쪽부터 자리 잡는다
const LIVE = { start: 2000, scramble: 900, hold: 900 } // ms, rate 반영 전
const HEX = '0123456789abcdef'
const randomHash = () => {
  let h = '0x'
  for (let i = 0; i < 4; i++) h += HEX[(Math.random() * 16) | 0]
  h += '…'
  for (let i = 0; i < 4; i++) h += HEX[(Math.random() * 16) | 0]
  return h
}


export function ChainCallout() {
  const { scene } = useGLTF(MODEL, DRACO)
  const layout = useMemo(() => {
    const right = rightOfYaw(CAM_STACK.yaw) // 이 챕터 카메라(chapters.ts)
    const layers = LAYERS.map((l) => {
      const target = partOf(scene, ...l.part)
      return { ...l, target, side: l.side ?? (sideFor(topOf(target), right) as Side) }
    })
    // tx 핀 하나(해시 + 블록 번호 두 줄): 백플레이트 +X 끝 — 움직이지 않는 층. 둘로 나누면 작은 화면(720 높이)에서 같은 끝에 몰려 겹쳤다
    const board = boxOf(scene, (n) => n === 'Backplate')
    const cz = board.getCenter(new Vector3()).z
    const len = board.max.x - board.min.x
    const tx: Vec3 = [board.max.x - len * 0.06, board.max.y, cz]
    return { layers, tx, txSide: sideFor(tx, right) }
  }, [scene])

  const layerPins = usePins(LAYERS.length)
  const txPins = usePins(1)
  const cells = useRef<{ hash?: HTMLElement | null; block?: HTMLElement | null }>({})
  const shown = useRef(false)
  const layerOn = useRef<boolean[]>(LAYERS.map(() => false))
  const tl = useRef<Timeline | null>(null)
  const live = useRef<Timeline | null>(null) // 해시·블록 순환(loop)
  const layerTl = useRef<(Timeline | null)[]>(LAYERS.map(() => null))

  useEffect(
    () => () => {
      tl.current?.pause()
      live.current?.pause()
      layerTl.current.forEach((t) => t?.pause())
    },
    [],
  )

  useFrame(() => {
    const { hash, block } = cells.current
    if (!pinsReady(layerPins) || !pinsReady(txPins) || !hash || !block) return
    const on = chapterStore.get() === CHAPTER && P.stack >= SHOW_AT
    const rate = motionRate()
    if (on !== shown.current) {
      shown.current = on
      tl.current?.pause()
      live.current?.pause()
      if (on) {
        const t = enterAll(txPins, rate, TX_AT)
        scrambleIn(t, hash, HASH, { at: BLOCK.at, duration: BLOCK.duration, rate })
        countUp(t, block, BLOCK.from, BLOCK.to, { at: BLOCK.at, duration: BLOCK.duration, rate, format: (v) => '#' + Math.round(v).toLocaleString('ko-KR') })
        // 살아 있는 체인: 바퀴마다 블록 +1 · 새 해시 스크램블. 대상 문자열은 바퀴 시작 call 이 바꾸고, 같은 p 트윈이 그걸 읽어 쓴다(loop 로 p 는 매 바퀴 0 부터)
        const st = { p: 0, text: HASH, block: BLOCK.to }
        const isHex = [...HASH].map((ch) => /[0-9a-f]/.test(ch)) // 자리 모양은 항상 같다(0x····…····) — 매 프레임 정규식 대신 한 번만
        const write = () => {
          const k = Math.floor(st.p * st.text.length)
          let out = ''
          for (let i = 0; i < st.text.length; i++) out += i < k || !isHex[i] ? st.text[i] : HEX[(Math.random() * 16) | 0]
          hash.textContent = out
        }
        const c = createTimeline({ loop: true, loopDelay: LIVE.hold * rate, autoplay: false })
        c.call(() => {
          st.text = randomHash()
          st.block += 1
          block.textContent = '#' + st.block.toLocaleString('ko-KR')
        }, 0)
        c.add(st, { p: [0, 1], duration: LIVE.scramble * rate, ease: 'linear', onUpdate: write }, 0)
        t.call(() => c.play(), LIVE.start * rate) // 첫 등장(카운트업·첫 해시)이 끝난 뒤부터
        live.current = c
        tl.current = t
      } else {
        tl.current = exitAll(txPins, rate)
        // 층 핀도 전부 끈다(켜져 있던 것만)
        layerPins.forEach((p, i) => {
          if (!layerOn.current[i]) return
          layerOn.current[i] = false
          layerTl.current[i]?.pause()
          const n = p.current
          if (!calloutReady(n)) return
          const t = createTimeline({ onComplete: () => hideCallout(n) })
          addExit(t, n, rate, i * 40)
          layerTl.current[i] = t
        })
      }
    }
    if (!on) return
    // 층이 자리 잡는 만큼 한 핀씩 — 되감으면 같은 지점에서 다시 들어간다
    LAYERS.forEach((l, i) => {
      const want = P.stack >= l.revealAt
      if (want === layerOn.current[i]) return
      layerOn.current[i] = want
      const n = layerPins[i].current
      if (!calloutReady(n)) return
      layerTl.current[i]?.pause()
      if (want) {
        const t = createTimeline()
        addEnter(t, n, rate)
        layerTl.current[i] = t
      } else {
        const t = createTimeline({ onComplete: () => hideCallout(n) })
        addExit(t, n, rate)
        layerTl.current[i] = t
      }
    })
  })

  return (
    <>
      {layout.layers.map((l, i) => (
        <Pointer key={l.label} pin={layerPins[i]} target={l.target} lift={l.lift} offset={l.offset} side={l.side}>
          <li className={styles.item}>
            <span className={styles.tick} />
            {l.label}
          </li>
        </Pointer>
      ))}
      <Pointer pin={txPins[0]} target={layout.tx} side={layout.txSide} caption="tx">
        <li className={styles.item}>
          <span className={styles.tick} />
          <b
            className={styles.mono}
            ref={(el) => {
              cells.current.hash = el
            }}
          >
            {HASH}
          </b>
        </li>
        <li className={`${styles.item} ${styles.dim}`}>
          <span className={styles.tick} />
          블록{' '}
          <b
            className={styles.mono}
            ref={(el) => {
              cells.current.block = el
            }}
          >
            #{BLOCK.to.toLocaleString('ko-KR')}
          </b>
        </li>
      </Pointer>
    </>
  )
}
