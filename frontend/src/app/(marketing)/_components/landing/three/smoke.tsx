'use client'

import { useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import { BufferAttribute, BufferGeometry, NormalBlending, Points, ShaderMaterial } from 'three'
import { P } from '../progress'

// 챕터 4 SMOKE: 백플레이트 가장자리(카드 양 옆 긴 변)에서 연기가 바깥으로 밀려 나와 그 자리에서 흩어져 사라진다(쉬라우드를 따라가지 않음).
// 형태는 셰이더 fbm 노이즈(뜯어진 가장자리·얼룩), 모션은 밀림→감속 + 난류 + 회전 + 부력.
// 분사구(emitter) 하나가 같은 경로로 퍼프 K개를 시차를 두고 내보내 이어진 기둥이 되고, 퍼프끼리 겹쳐 부피감이 난다.
// 스크롤 sync 라 시간 기반 스폰 없이 — 퍼프마다 (t0, life) 를 갖고 P.smoke 의 함수로 위치/크기/알파를 계산한다(되감기 가능).
const E = 64 // 분사구 수(양 옆 반씩)
const K = 10 // 분사구당 퍼프 수(사슬)
const N = E * K
const SEAM = { backY: -0.23, halfZ: 0.64, halfX: 1.45 } // 백플레이트 가장자리. 카드 치수(glTF): 길이 ±1.55, 폭 ±0.64

const rand = (seed: number) => {
  // mulberry32 — 결정론적 난수(리로드해도 같은 연기)
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

type Puff = { ox: number; oy: number; oz: number; dx: number; dy: number; dz: number; t0: number; life: number; size: number; seed: number; spin: number }

export function Smoke() {
  const ref = useRef<Points>(null)
  const { puffs, geo, mat } = useMemo(() => {
    const r = rand(7)
    const puffs: Puff[] = []
    for (let e = 0; e < E; e++) {
      const side = e % 2 === 0 ? -1 : 1 // 카드 양 옆으로 번갈아
      const ox = (r() * 2 - 1) * SEAM.halfX
      const dx = (r() - 0.5) * 0.3
      const dy = -0.12 + r() * 0.1 // 살짝 아래로 깔리듯 나오고, 이후 부력으로 조금 뜬다
      const dz = side * (0.7 + r() * 0.5)
      const t0 = r() * 0.45
      for (let j = 0; j < K; j++) {
        puffs.push({
          ox,
          oy: SEAM.backY,
          oz: side * SEAM.halfZ,
          dx,
          dy,
          dz,
          t0: t0 + j * 0.03, // 사슬: 뒤 퍼프가 조금씩 늦게 같은 길을 따라간다
          life: 0.18 + r() * 0.1, // t0+life ≤ 1 → smoke=1 에서 전부 사라짐
          size: 0.36 + r() * 0.3, // 월드 직경(진행하며 ~2배까지 커짐)
          seed: r(),
          spin: (r() - 0.5) * 2,
        })
      }
    }
    const geo = new BufferGeometry()
    geo.setAttribute('position', new BufferAttribute(new Float32Array(N * 3), 3))
    geo.setAttribute('aSize', new BufferAttribute(new Float32Array(N), 1))
    geo.setAttribute('aAlpha', new BufferAttribute(new Float32Array(N), 1))
    geo.setAttribute('aRot', new BufferAttribute(new Float32Array(N), 1))
    geo.setAttribute('aSeed', new BufferAttribute(Float32Array.from(puffs.map((p) => p.seed)), 1))
    const mat = new ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: NormalBlending,
      uniforms: { uScale: { value: 400 } },
      vertexShader: `
        attribute float aSize; attribute float aAlpha; attribute float aRot; attribute float aSeed;
        varying float vAlpha; varying float vRot; varying float vSeed; uniform float uScale;
        void main() {
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_Position = projectionMatrix * mv;
          gl_PointSize = aSize * uScale / -mv.z;
          vAlpha = aAlpha; vRot = aRot; vSeed = aSeed;
        }`,
      // 연기 형태: 원판을 fbm 노이즈로 뜯어내고(가장자리 불규칙), 안쪽 농도도 노이즈로 얼룩지게
      fragmentShader: `
        varying float vAlpha; varying float vRot; varying float vSeed;
        float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
        float vnoise(vec2 p) {
          vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
          return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
        }
        float fbm(vec2 p) { return 0.5 * vnoise(p) + 0.25 * vnoise(p * 2.1 + 3.7) + 0.125 * vnoise(p * 4.3 + 9.1); }
        void main() {
          vec2 uv0 = gl_PointCoord - 0.5;
          float c = cos(vRot), s = sin(vRot);
          vec2 uv = mat2(c, -s, s, c) * uv0;
          float n = fbm(uv * 2.6 + vSeed * 19.0);          // 0..~0.9 큰 덩어리
          float n2 = fbm(uv * 6.0 + vSeed * 7.0);          // 잔결
          float rad = 0.5 + 0.16 * (n - 0.45) * 2.0;        // 노이즈로 울퉁불퉁한 반지름
          float d = length(uv) / rad;
          float dens = exp(-d * d * 3.2) * (1.0 - smoothstep(0.7, 1.0, d)); // 중심 짙고 가장자리 안개처럼
          dens *= 0.55 + 0.6 * n + 0.25 * (n2 - 0.45);      // 얼룩
          float light = 0.8 + 0.45 * uv0.y + 0.3 * (n - 0.45); // 위쪽이 밝다(위에서 비추는 조명)
          float a = clamp(dens, 0.0, 1.0) * vAlpha;
          gl_FragColor = vec4(vec3(0.66, 0.69, 0.72) * light, a);
        }`,
    })
    return { puffs, geo, mat }
  }, [])

  useFrame(({ size, viewport, camera }) => {
    const s = P.smoke
    const pos = geo.attributes.position.array as Float32Array
    const sz = geo.attributes.aSize.array as Float32Array
    const al = geo.attributes.aAlpha.array as Float32Array
    const rot = geo.attributes.aRot.array as Float32Array
    for (let i = 0; i < N; i++) {
      const p = puffs[i]
      const t = Math.min(Math.max((s - p.t0) / p.life, 0), 1)
      const k = 1 - Math.pow(1 - t, 2.6) // 밀려 나오다 감속
      // 난류: 퍼프별 위상으로 느리게 흔들림(진행할수록 커짐). 부력: 깔리다가 조금 뜨며 흩어진다
      const w = t * 0.1
      pos[i * 3] = p.ox + p.dx * k + Math.sin(t * 4.3 + p.seed * 31) * w
      pos[i * 3 + 1] = p.oy + p.dy * k + 0.16 * t * t + Math.sin(t * 3.1 + p.seed * 17) * w * 0.5
      pos[i * 3 + 2] = p.oz + p.dz * k + Math.cos(t * 3.7 + p.seed * 23) * w
      sz[i] = p.size * (0.35 + 1.9 * t)
      al[i] = t <= 0 || t >= 1 ? 0 : Math.min(1, t * 6) * Math.pow(1 - t, 1.0) * 0.55 // 겹쳐서 쌓이게
      rot[i] = p.seed * 6.283 + p.spin * t
    }
    geo.attributes.position.needsUpdate = true
    geo.attributes.aSize.needsUpdate = true
    geo.attributes.aAlpha.needsUpdate = true
    geo.attributes.aRot.needsUpdate = true
    // 월드 직경 → 픽셀: h/2 / (z·tan(fov/2))  (z 나누기는 셰이더에서)
    const fov = 'fov' in camera ? (camera.fov as number) : 32
    mat.uniforms.uScale.value = (size.height * viewport.dpr * 0.5) / Math.tan((fov * Math.PI) / 360)
    if (ref.current) ref.current.visible = s > 0
  })

  return <points ref={ref} geometry={geo} material={mat} frustumCulled={false} />
}
