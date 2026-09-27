'use client'

import { useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import { CanvasTexture, Mesh, MeshBasicMaterial, MeshStandardMaterial, PerspectiveCamera, RepeatWrapping, SRGBColorSpace, Vector3 } from 'three'
import { P } from '../progress'
import { DRACO, MODEL } from './gpuModel'

// 배경판 3장 — 큰 평면을 매 프레임 카메라 앞 dist 에 카메라 방향으로 세운다(카메라 자식과 같은 효과 — 오빗 때 판이 비스듬해지지 않는다).
//   shroud: 사이드바 01 카드 구간을 내려간 정도가 곧 밝기(P.plate). GLB Shroud 재질(무광 차콜) clone + 부조 bumpMap
//   black : 03 오빗 구간 진행률(P.plate2W)로 **아래에서 위로** 그라디언트 램프(alphaMap)가 올라와 차콜판을 덮는 검정 단색. 되돌리면 내려간다
//   lit   : 04 히트싱크부터 섹션 끝까지 진행률(P.light)로 "조명이 조금씩 켜지듯" 검정 위로 드러나는 스틸판. 화면 중앙의 부드러운 원형 스팟(alphaMap)을
//           씌우고 스크롤에 따라 밝기(opacity)와 스팟 반경을 함께 키운다 — 끝에서도 모서리는 살짝 어둡다(비네트).
//           **실제 조명을 쓰지 않는다(MeshBasic)** — 조명을 쓰면 카메라가 돌 때 반사가 같이 돌아 카드 오빗에 배경이 끌려갔고, 점광원은 켜지는 순간 전체가 밝아졌다
//   흐름  : 차콜판·스틸판은 스크롤에 따라 챕터당 반 화면씩 위로 흐른다(P.scroll × SCROLL_PER_CHAPTER). 검정판은 단색이라 고정
// 부조 도안(2026-09-12): 빽빽한 문구 각인은 "텍스처가 너무 많다"고 걷어냈다. 지금은 **판마다 다른 문양 하나가 화면 한가운데**를 지나간다(사용자 요청).
//   차콜판(01–03): 가로 패널 이음매 + 큰 G 마크. **진짜 요철**(bumpMap)이라 조명이 스칠 때만 드러난다
//   스틸판(04–07): G 마크만. 조명을 안 쓰므로 그림자·하이라이트를 **구워 넣는다**(2026-09-12 사용자 요청으로 동심 홈·눈금 계측 마크에서 교체)
//   둘 다 **음각**(파냄, 2026-09-12 사용자 결정) — 빛은 위에서 온다고 보고 위쪽 벽을 어둡게, 아래쪽 벽을 밝게 둔다
// 타일은 양쪽 다 화면 높이의 약 1.7 배(TILE_SCREENS) — 스크롤(챕터당 반 화면)에 따라 문양이 화면을 가로질러 흐르되 늘 하나가 시야 안에 있다.
// **2026-09-14 사용자 결정 — 배경은 차콜판 한 장 고정**: gpuCanvas 가 shroud 만 마운트하고, 그 판도 flow:false + P.plate 고정 1(progress.ts) 이라 스크롤해도 로고가 정중앙에 그대로 있다.
// black·lit 경로와 아래 노브는 되돌릴 수 있게 남겨둔다(검정 와이프·스틸판 조명) — gpuCanvas 에 <Backdrop kind="black"/> <Backdrop kind="lit"/> 두 줄을 되살리면 끝
const SCROLL_PER_CHAPTER = 0.5 // 차콜판이 챕터(구간) 하나당 흐르는 양(화면 높이 배). 속도 노브. 전 구간 7 챕터 → 3.5 화면
const SIZE = 120 // 판 한 변. dist 23·fov 32° 에서 화면 높이 ≈ 13.2 → 최대 오프셋 3.5 화면(≈ 46) + 화면 반(6.6) < 반 변 60 이라 가장자리가 안 보인다
// 와이프 형상 — 단위는 "화면 높이"(0 = 화면 아래, 1 = 화면 위). 진행 w 0..1
const RAMP_HALF = 0.2 // 램프 반폭(투명 → 덮임으로 바뀌는 그라데이션 구간 = 0.4 화면). 노브
// 도안 공통 — 노브
const TILE_PX = 640 // 타일 해상도. 일부러 낮게 잡아 확대되며 부드러워진다 — 배경이 초점 밖처럼 보여 멀리 있는 느낌이 난다(2026-09-12 사용자 요청). 아래 px 값들은 이 해상도 기준 비율로 따라간다
const EMBOSS_PX = Math.max(1, Math.round(TILE_PX * 0.003)) // 음각 그림자·하이라이트를 미는 거리. 빛은 왼쪽 위에서 온다고 본다 → 파인 자리는 위·왼쪽이 어둡고 아래·오른쪽이 밝다
const RELIEF = 0.16 // 구운 음각(스틸판)의 음영 진폭(0..1). 0 이면 민판
// 로고 문양 — 노브(판 두 장 공통)
const LOGO_H = 0.5 // 로고 높이(타일 대비). 0.5 = 화면 높이의 ~0.85 → 카드를 감싸는 크기
const LOGO_DX = -0.05 // 로고 가로 보정(타일 대비, 음수 = 왼쪽). 마크 왼쪽에 속도선이 붙어 있어 이미지의 가운데와 원의 가운데가 어긋난다 — 원이 화면 정중앙에 오도록 민다(2026-09-12)
const LOGO_SRC = '/logo-mark.png'
// 차콜판 도안(01–03) — 노브
const SEAM_PX = Math.max(1, Math.round(TILE_PX * 0.0034)) // 패널 이음매 홈 두께. 스틸판엔 없다
const BUMP_SCALE = 2.6 // 차콜판 요철 깊이. 1.1 → 2.6(2026-09-12 "좀 깊게")
const BUMP_BASE = '#808080' // 높이맵 기준면(회색). 밝을수록 솟고 어두울수록 파인다 — 이음매·로고 모두 파는 쪽
const BUMP_DOWN = '#2a2a2a' // 파인 바닥. 기준면과 차이가 클수록 가장자리 음영이 세다
// 맨들한 스틸판 — 노브
const STEEL_TONE = '#3d4045' // 바탕 회색. 차콜판(01–03)과 같은 계열의 스틸이되 한 단계 어둡게(2026-09-12 사용자 요청, #5a5d61 → #3d4045)
const STEEL_SHEEN = 0.13 // 타일마다 지나가는 부드러운 광택 띠의 명암 진폭(0..1). 0 이면 완전 단색
// lit 스팟(조명 켜짐) — 노브. 스팟 지름 = 화면 높이 × (SPOT_MIN + (SPOT_MAX − SPOT_MIN) × P.light). 지름 3.5 화면이면 16:9 모서리(중심에서 ≈ 1.02 화면)가 알파 ≈ 0.7 → 살짝 비네트
const SPOT_MIN = 0.9
const SPOT_MAX = 3.5
const SPOT_CORE = 0.2 // 스팟 텍스처에서 알파 1 인 중심 반경(0..0.5). 그 밖은 가장자리(0.5)까지 부드럽게 0

// 판 종류가 곧 재질·등장 방식이다(shroud = GLB 차콜 재질·불투명도 스크럽, black = 순검정·아래→위 램프 와이프, lit = 스틸 텍스처·원형 스팟). 스펙엔 값만 둔다
type Kind = 'shroud' | 'black' | 'lit'
type Spec = {
  key: 'plate' | 'plate2W' | 'light' // landingScene 이 스크럽으로 기록하는 P 필드(0..1): shroud 는 불투명도, black 은 램프 위치, lit 은 밝기·스팟 반경
  flow: boolean // 스크롤(P.scroll)에 따라 흐르는지(단색 검정은 흘러도 보이지 않으니 고정)
  dist: number // 카메라에서 판까지(glTF 단위). 카드(dist ≤ 6.5 + 반경 ~1.5)보다 충분히 뒤. 깊이는 안 쓰고(depthWrite false) 겹침은 order 가 정한다
  order: number // renderOrder — 뒤 판부터 그려 앞 판이 위에 블렌딩
}
const PLATES: Record<Kind, Spec> = {
  shroud: { key: 'plate', flow: false, dist: 24, order: -3 }, // flow false = 스크롤에 안 흘른다 → 로고가 항상 화면 정중앙(2026-09-14)
  black: { key: 'plate2W', flow: false, dist: 23, order: -2 },
  lit: { key: 'light', flow: true, dist: 22, order: -1 },
}

const FOV = 32 // gpuCanvas 의 카메라 fov — 타일을 "화면 몇 배" 로 잡기 위한 환산에만 쓴다(연출 값은 프레임 루프에서 camera.fov 로 다시 읽는다)
const TILE_SCREENS = 1.7 // 타일 한 변 = 화면 높이의 몇 배. 판마다 거리가 달라 유닛 크기는 다르지만 화면에서 보이는 크기는 같다
/** 도안 타일을 판에 반복시키고 문양을 화면 한가운데에 맞춘다(화면 중앙 = 판 중앙 = uv 0.5, 문양은 타일 가운데) */
function tileTexture(c: HTMLCanvasElement, dist: number): CanvasTexture {
  const tex = new CanvasTexture(c)
  tex.wrapS = tex.wrapT = RepeatWrapping
  const units = TILE_SCREENS * 2 * dist * Math.tan((FOV * Math.PI) / 360)
  const r = SIZE / units
  tex.repeat.set(r, r)
  tex.offset.set(0.5 - 0.5 * r, 0.5 - 0.5 * r) // 스크롤 0(흐름 없음)에서 문양이 화면 정중앙
  return tex
}

let markPromise: Promise<HTMLImageElement | null> | null = null
/** 로고 마크 1회 로드(모듈 캐시). 실패하면 null — 도안은 이음매만 남는다 */
function loadMark() {
  markPromise ??= new Promise<HTMLImageElement | null>((resolve) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => resolve(null)
    img.src = LOGO_SRC
  })
  return markPromise
}
/** 로고의 알파(실루엣)만 남겨 한 가지 색으로 칠한 캔버스 — 부조는 같은 실루엣을 색만 바꿔 겹쳐 그린다 */
function tintMark(img: HTMLImageElement, h: number, color: string) {
  const c = document.createElement('canvas')
  c.height = Math.round(h)
  c.width = Math.round((h * img.width) / img.height)
  const g = c.getContext('2d')!
  g.drawImage(img, 0, 0, c.width, c.height)
  g.globalCompositeOperation = 'source-in'
  g.fillStyle = color
  g.fillRect(0, 0, c.width, c.height)
  return c
}
/** 로고를 그릴 타일 좌표(LOGO_DX 보정 포함) */
const markX = (w: number) => (TILE_PX - w) / 2 + LOGO_DX * TILE_PX
const markY = (h: number) => (TILE_PX - h) / 2
/** 타일 한가운데에 로고를 그린다. dx·dy 는 부조 음영을 미는 거리 */
function drawMark(g: CanvasRenderingContext2D, m: HTMLCanvasElement, dx = 0, dy = 0) {
  g.drawImage(m, markX(m.width) + dx, markY(m.height) + dy)
}

/** 차콜판 부조 높이맵(bumpMap): 기준 회색 위에 가로 이음매와 가운데 G 마크를 어둡게 = 둘 다 파인 자리.
 *  색은 건드리지 않아 조명이 스칠 때만 드러난다. 이음매는 타일 위·아래 가장자리 두 곳에 그려야 이웃 타일에서 끊기지 않는다 */
function makeBumpPlate(dist: number) {
  const c = document.createElement('canvas')
  c.width = c.height = TILE_PX
  const g = c.getContext('2d')!
  const draw = (img: HTMLImageElement | null) => {
    g.fillStyle = BUMP_BASE
    g.fillRect(0, 0, TILE_PX, TILE_PX)
    g.fillStyle = BUMP_DOWN
    g.fillRect(0, -SEAM_PX / 2, TILE_PX, SEAM_PX)
    g.fillRect(0, TILE_PX - SEAM_PX / 2, TILE_PX, SEAM_PX)
    if (img) drawMark(g, tintMark(img, TILE_PX * LOGO_H, BUMP_DOWN))
  }
  draw(null)
  return { tex: tileTexture(c, dist), draw }
}

/** 스틸판 색 텍스처(조명 없이 그대로 보이는 값): 맨들한 바탕 + 타일마다 지나가는 광택 띠(위아래 끝 값이 같아 이어 붙여도 경계가 없다) + 가운데 로고(음각).
 *  빛이 왼쪽 위에서 온다고 보고 파인 자리의 위·왼쪽 벽은 그늘, 아래·오른쪽 벽은 밝게 — 단 **음영은 전부 실루엣 안쪽으로만** 자른다(밖으로 번지면 드롭섀도처럼 보여 로고가 떠 보인다).
 *  로고가 타일 가장자리에 닿지 않아 반복 경계는 보이지 않는다. draw 는 로고가 늦게 로드되면 다시 부른다 */
function makeSteelPlate(dist: number) {
  const c = document.createElement('canvas')
  c.width = c.height = TILE_PX
  const g = c.getContext('2d')!
  const draw = (img: HTMLImageElement | null) => {
    g.fillStyle = STEEL_TONE
    g.fillRect(0, 0, TILE_PX, TILE_PX)
    const sheen = g.createLinearGradient(0, TILE_PX, 0, 0)
    sheen.addColorStop(0, `rgba(0,0,0,${STEEL_SHEEN * 0.8})`)
    sheen.addColorStop(0.5, `rgba(255,255,255,${STEEL_SHEEN})`)
    sheen.addColorStop(1, `rgba(0,0,0,${STEEL_SHEEN * 0.8})`)
    g.fillStyle = sheen
    g.fillRect(0, 0, TILE_PX, TILE_PX)
    if (!img) return
    const h = TILE_PX * LOGO_H
    const sil = tintMark(img, h, '#000') // 실루엣(알파만 쓴다)
    const x = markX(sil.width)
    const y = markY(sil.height)
    // 파인 자리의 '안쪽 가장자리 띠'만 만든다: 실루엣에서 자신을 (dx,dy) 만큼 민 사본을 빼면 그 반대쪽 안쪽 테두리만 남는다.
    // 실루엣 밖으로는 한 픽셀도 나가지 않아 그림자처럼 번지지 않는다(밖으로 번지면 떠 보인다 — 2026-09-12 수정)
    const band = (color: string, dx: number, dy: number) => {
      const b = document.createElement('canvas')
      b.width = sil.width
      b.height = sil.height
      const bg = b.getContext('2d')!
      bg.drawImage(tintMark(img, h, color), 0, 0)
      bg.globalCompositeOperation = 'destination-out'
      bg.drawImage(sil, dx, dy)
      return b
    }
    drawMark(g, tintMark(img, h, `rgba(0,0,0,${RELIEF * 0.35})`)) // 파인 바닥 — 바탕보다 조금 어둡다
    g.drawImage(band(`rgba(0,0,0,${RELIEF * 2})`, EMBOSS_PX, EMBOSS_PX), x, y) // 위·왼쪽 안쪽 벽 = 그늘
    g.drawImage(band(`rgba(255,255,255,${RELIEF * 1.6})`, -EMBOSS_PX, -EMBOSS_PX), x, y) // 아래·오른쪽 안쪽 벽 = 빛
  }
  draw(null)
  const tex = tileTexture(c, dist)
  tex.colorSpace = SRGBColorSpace
  return { tex, draw }
}

/** lit 판용 원형 스팟(알파맵은 G 채널): 중심 SPOT_CORE 까지 1, 가장자리(0.5)에서 0. wrap 은 ClampToEdge 라 밖은 0(어둠) */
function makeSpot(): CanvasTexture {
  const N = 512
  const c = document.createElement('canvas')
  c.width = c.height = N
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(N / 2, N / 2, N * SPOT_CORE, N / 2, N / 2, N / 2)
  grad.addColorStop(0, '#fff')
  grad.addColorStop(1, '#000')
  g.fillStyle = grad
  g.fillRect(0, 0, N, N)
  return new CanvasTexture(c)
}

/** 세로 1×N 그라디언트(알파맵은 G 채널). v = 0(아래)..1(위), 텍스처 한 장 = 화면 높이 하나. 기본 wrap(ClampToEdge)이라 밖은 양 끝 값으로 이어진다 */
function makeGradient(alpha: (v: number) => number): CanvasTexture {
  const N = 512
  const c = document.createElement('canvas')
  c.width = 1
  c.height = N
  const g = c.getContext('2d')!
  for (let i = 0; i < N; i++) {
    const v = 1 - (i + 0.5) / N // 캔버스 y 는 위가 0 → 텍스처 v 는 아래가 0
    const a = Math.round(255 * Math.min(1, Math.max(0, alpha(v))))
    g.fillStyle = `rgb(${a},${a},${a})`
    g.fillRect(0, i, 1, 1)
  }
  return new CanvasTexture(c)
}
const smooth = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t))
/** 위로 올라오는 램프: 중심(v .5) 아래는 1(덮임), 위는 0 */
const rampUp = (v: number) => 1 - smooth((v - (0.5 - RAMP_HALF)) / (2 * RAMP_HALF))

/** 화면 기준 위치 c(0 = 화면 아래, 1 = 화면 위)에 텍스처 중심(v .5)이 오도록 offset.y. 텍스처 한 장 = 화면 높이라 repeat.y = SIZE / h */
function placeGradient(tex: CanvasTexture, c: number, h: number) {
  const R = SIZE / h
  tex.repeat.set(1, R)
  const vBottom = 0.5 - 0.5 / R // 평면 UV 에서 화면 아래 위치
  tex.offset.set(0, 0.5 - c - vBottom * R)
}

export function Backdrop({ kind }: { kind: Kind }) {
  const s = PLATES[kind]
  const { scene } = useGLTF(MODEL, DRACO) // shroud 만 쓰지만 훅은 조건부로 부를 수 없다 — 캐시된 scene 이라 비용은 없다
  // 재질 + 여기서 만든 텍스처들(언마운트 때 우리가 만든 것만 dispose — GLB 재질의 텍스처는 모델과 공유라 건드리지 않는다)
  const { material, alpha, owned, redraw } = useMemo(() => {
    let mat: MeshStandardMaterial | MeshBasicMaterial
    let redraw: ((img: HTMLImageElement) => void) | null = null
    const owned: CanvasTexture[] = []
    let alpha: CanvasTexture | null = null
    if (kind === 'shroud') {
      let base: MeshStandardMaterial | undefined
      scene.getObjectByName('Shroud')?.traverse((o) => {
        if (base || !(o as Mesh).isMesh) return
        const m = (o as Mesh).material
        base = (Array.isArray(m) ? m[0] : m) as MeshStandardMaterial
      })
      // 폴백 = gpu_model.py 의 Shroud 재질 값(0.035 linear ≈ #333438, metallic .15, roughness .42)
      mat = (base ?? new MeshStandardMaterial({ color: '#333438', metalness: 0.15, roughness: 0.42 })).clone()
      const { tex, draw } = makeBumpPlate(s.dist)
      owned.push(tex)
      mat.bumpMap = tex // 진짜 요철 — 조명이 비스듬할 때만 드러난다
      mat.bumpScale = BUMP_SCALE
      redraw = (img) => {
        draw(img)
        tex.needsUpdate = true
      }
    } else if (kind === 'lit') {
      const { tex, draw } = makeSteelPlate(s.dist)
      owned.push(tex)
      mat = new MeshBasicMaterial({ map: tex }) // 조명·카메라 무관 — 광택과 부조가 텍스처에 구워져 있다
      redraw = (img) => {
        draw(img)
        tex.needsUpdate = true
      }
      alpha = makeSpot() // 불투명도 = opacity(밝기) × 스팟
    } else {
      mat = new MeshBasicMaterial({ color: '#000' }) // 조명·환경맵 무관한 순검정
      alpha = makeGradient(rampUp) // 불투명도 = 램프(아래에서 위로)
    }
    if (alpha) {
      owned.push(alpha)
      mat.alphaMap = alpha
    }
    // 차콜판은 P.plate 가 1 고정(2026-09-14)이라 늘 불투명 → 투명 렌더 큐·정렬을 거치지 않는다.
    // **P.plate 스크럽을 되살리면 여기도 transparent 로 되돌려야 한다** — 안 그러면 밝기 변화가 안 먹는다.
    // 검정판·스틸판은 alphaMap·opacity 로 흐려지므로 그대로 투명
    mat.transparent = kind !== 'shroud'
    mat.opacity = kind === 'shroud' ? 1 : 0
    mat.depthWrite = false
    mat.needsUpdate = true // 맵을 붙였으니 셰이더 재컴파일
    return { material: mat, alpha, owned, redraw }
  }, [scene, kind, s.dist])
  // 로고는 이미지라 늦게 온다 — 도착하면 도안을 다시 그리고 텍스처만 갱신한다(재질·메시는 그대로)
  useEffect(() => {
    if (!redraw) return
    let alive = true
    loadMark().then((img) => {
      if (alive && img) redraw(img)
    })
    return () => {
      alive = false
    }
  }, [redraw])
  useEffect(
    () => () => {
      owned.forEach((t) => t.dispose())
      material.dispose()
    },
    [material, owned],
  )
  const ref = useRef<Mesh>(null)
  const fwd = useMemo(() => new Vector3(), [])
  const up = useMemo(() => new Vector3(), [])
  useFrame(({ camera }) => {
    const m = ref.current
    if (!m) return
    const a = P[s.key]
    // 값이 0 이면 그리지 않는다. 차콜판은 검정이 화면을 다 덮은 뒤(plate2W = 1, 스크럽 값이라 스크롤과 동시)에도 그리지 않는다 — 되돌리면 검정이 벗겨지는 순간 바로 다시 보인다
    m.visible = a > 0 && (kind !== 'shroud' || P.plate2W < 1)
    if (!m.visible) return
    material.opacity = kind === 'black' ? 1 : a // 검정판의 진행값은 램프 위치라 불투명도는 항상 1
    const h = 2 * s.dist * Math.tan(((camera as PerspectiveCamera).fov * Math.PI) / 360) // 이 거리에서의 화면 높이
    camera.getWorldDirection(fwd)
    up.set(0, 1, 0).applyQuaternion(camera.quaternion)
    const flow = s.flow ? P.scroll * SCROLL_PER_CHAPTER * h : 0
    m.position.copy(camera.position).addScaledVector(fwd, s.dist).addScaledVector(up, flow)
    m.quaternion.copy(camera.quaternion) // 평면 +Z(앞면)가 카메라를 향한다
    if (!alpha) return
    if (kind === 'black') {
      // 램프 중심의 화면 위치: 화면 아래 밖(-RAMP_HALF) → 위 밖(1+RAMP_HALF). 판이 흐른 만큼(flow) 되돌려 램프는 화면에 고정
      placeGradient(alpha, -RAMP_HALF + (1 + 2 * RAMP_HALF) * a - flow / h, h)
    } else {
      // 스팟은 화면 중앙에 고정(판이 흐른 만큼 되돌림), 지름은 밝기와 함께 커진다
      const R = SIZE / (h * (SPOT_MIN + (SPOT_MAX - SPOT_MIN) * a))
      alpha.repeat.set(R, R)
      alpha.offset.set(0.5 - 0.5 * R, 0.5 - (0.5 - flow / SIZE) * R)
    }
  })
  return (
    <mesh ref={ref} material={material} renderOrder={s.order}>
      <planeGeometry args={[SIZE, SIZE]} />
    </mesh>
  )
}
