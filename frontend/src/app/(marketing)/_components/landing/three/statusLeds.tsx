'use client'

import { useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import { Color, type InstancedMesh, MeshBasicMaterial, Object3D, SphereGeometry } from 'three'

// PCB 위 작은 붉은 상태 LED 점들 — 칩셋 옆에서 서로 다른 패턴으로 깜빡여 '일하는' 느낌. 톤매핑을 안 타는 순색 점(작게).
// 위치는 Blender (x, y) → glTF (x, -y). PCB 윗면 y(glTF) = Z_PCB(-0.20) + 0.008
const Y = -0.20 + 0.008 + 0.004
// [x, z(glTF), 패턴]  pattern: 'heartbeat'(느린 두 번 깜빡) | 'blink'(1Hz 깜빡) | 'busy'(불규칙 빠른 점멸) | 'slow'(2.5s 숨쉬기)
const LEDS: [number, number, 'heartbeat' | 'blink' | 'busy' | 'slow'][] = [
  [0.80, 0.57, 'busy'], // PWM IC 옆(우하단)
  [-0.89, -0.44, 'heartbeat'], // BIOS IC 옆(좌상단)
  [-0.80, 0.31, 'blink'], // 디스플레이 컨트롤러 옆(좌하단)
  [0.40, -0.55, 'slow'], // 전원 커넥터 옆(상단)
  [1.0, 0.45, 'blink'], // VRM 우하단 모서리
  [0.12, 0.42, 'busy'], // 기판 우하 모서리 근처
]
const RED = new Color('#ff2a1a')
const DIM = new Color('#3a0806') // 꺼진 상태(살짝 어두운 붉은 기)

// VRAM 8개 위 초록 활동 LED 각 8개 — 칩 윗면 긴 변 쪽에 한 줄, 메모리 액세스처럼 불규칙하게 깜빡
const GX = -0.25 // GPU 다이 x (Blender build_pcb 의 gx)
const VRAM = [[-0.4, 0.22], [-0.4, -0.18], [0.4, 0.22], [0.4, -0.18], [-0.15, 0.4], [0.15, 0.4], [-0.15, -0.36], [0.15, -0.36]] // (dx, dy) Blender
const VRAM_Y = -0.20 + 0.02 + 0.003 // VRAM 윗면(Z_PCB+0.02) 위
const PER_CHIP = 8
const GREEN = new Color('#35ff7a')
const GREEN_DIM = new Color('#062a12')

export function StatusLeds() {
  const ref = useRef<InstancedMesh>(null)
  const vref = useRef<InstancedMesh>(null)
  const { geo, vgeo, mat, dummy } = useMemo(
    () => ({ geo: new SphereGeometry(0.007, 10, 8), vgeo: new SphereGeometry(0.0035, 8, 6), mat: new MeshBasicMaterial({ toneMapped: false }), dummy: new Object3D() }),
    [],
  )
  const col = useMemo(() => new Color(), [])

  useFrame(({ clock }) => {
    const m = ref.current
    if (!m) return
    const t = clock.elapsedTime
    LEDS.forEach(([x, z, pat], i) => {
      dummy.position.set(x, Y, z)
      dummy.updateMatrix()
      m.setMatrixAt(i, dummy.matrix)
      let on = 0
      if (pat === 'blink') on = Math.sin(t * Math.PI * 2 + i) > 0.6 ? 1 : 0
      else if (pat === 'slow') on = 0.5 + 0.5 * Math.sin(t * 2.5 + i)
      else if (pat === 'heartbeat') {
        const ph = (t * 0.8 + i * 0.3) % 1 // 1.25s 주기 안에서 짧게 두 번
        on = ph < 0.08 || (ph > 0.18 && ph < 0.26) ? 1 : 0
      } else {
        // busy: 겹친 sin 으로 불규칙한 빠른 점멸
        on = Math.sin(t * 7.3 + i) * Math.sin(t * 3.1 + i * 2) > 0.15 ? 1 : 0
      }
      m.setColorAt(i, col.copy(DIM).lerp(RED, on))
    })
    m.instanceMatrix.needsUpdate = true
    if (m.instanceColor) m.instanceColor.needsUpdate = true

    const v = vref.current
    if (!v) return
    VRAM.forEach(([dx, dy], c) => {
      const cx = GX + dx
      const cz = -(0.02 + dy) // Blender y → glTF -z
      for (let k = 0; k < PER_CHIP; k++) {
        const i = c * PER_CHIP + k
        // 칩 윗면, 위쪽 긴 변 근처(z 는 -y 라 -0.045 가 Blender +y 쪽) 한 줄 8개, 피치 0.016
        dummy.position.set(cx - 0.056 + k * 0.016, VRAM_Y, cz - 0.045)
        dummy.updateMatrix()
        v.setMatrixAt(i, dummy.matrix)
        // 메모리 액세스 느낌: LED 마다 다른 두 주파수의 곱이 문턱을 넘을 때만 켜짐 → 불규칙하게 툭툭
        const s = i * 1.7
        const on = Math.sin(t * (5.5 + (i % 5) * 0.9) + s) * Math.sin(t * (2.3 + (i % 3) * 0.7) + s * 0.6) > 0.35 ? 1 : 0
        v.setColorAt(i, col.copy(GREEN_DIM).lerp(GREEN, on))
      }
    })
    v.instanceMatrix.needsUpdate = true
    if (v.instanceColor) v.instanceColor.needsUpdate = true
  })

  return (
    <>
      <instancedMesh ref={ref} args={[geo, mat, LEDS.length]} frustumCulled={false} />
      <instancedMesh ref={vref} args={[vgeo, mat, VRAM.length * PER_CHIP]} frustumCulled={false} />
    </>
  )
}
