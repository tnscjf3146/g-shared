'use client'

import { PerformanceMonitor } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { Suspense, useEffect, useRef, useState } from 'react'
import { ACESFilmicToneMapping } from 'three'
import { CameraRig } from './cameraRig'
import { GpuModel } from './gpuModel'
import { Lights, Studio } from './lights'
import { Smoke } from './smoke'
import { Starfield } from './starfield'
import { StatusLeds } from './statusLeds'

// r3f 캔버스 조립. 카메라는 cameraRig, 조명은 lights, 부품은 gpuModel — 모두 매 프레임 P 를 읽는다.
// 최적화: 스테이지가 뷰포트 밖이면 frameloop 을 멈춰 Footer 를 보는 동안 GPU/CPU 를 쓰지 않는다.
// 해상도 배율은 적응형: 프레임이 떨어지면(조립 완료 근접 컷 + 움직이는 스팟) PerformanceMonitor 가 dpr 을 1 까지 내리고, 여유가 생기면 다시 올린다(2026-09-11) — 팬 회전이 끊겨 보이던 처방
const DPR_MAX = 1.5
const DPR_MIN = 1
// (Hero 가 없어진 뒤로 캔버스가 첫 화면이라 로드를 미루지 않는다 — 인트로는 GpuModel 의 GPU_READY_EVENT 를 기다린다)
export function GpuCanvas() {
  const host = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [dpr, setDpr] = useState(DPR_MAX)
  useEffect(() => {
    const el = host.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), {
      rootMargin: '25%',
    }) // 조금 일찍 깨워 진입 시 첫 프레임이 준비돼 있게
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return (
    <div ref={host} className="h-full w-full">
      <Canvas
        frameloop={visible ? 'always' : 'never'}
        dpr={dpr}
        camera={{ fov: 32, near: 0.1, far: 200, position: [3, 2.5, 4] }} // far 200: 별 구름(반경 30~70)이 잘리지 않게
        gl={{
          antialias: true,
          toneMapping: ACESFilmicToneMapping,
          toneMappingExposure: 1.1,
        }}
        style={{ background: 'transparent' }} // 배경색은 뒤의 sticky 컨테이너(bg-black, 우주 배경)가 담당
      >
        {/* fps 가 기준 아래로 떨어지면 factor 가 줄어든다 → dpr 을 1..1.5 사이로 */}
        <PerformanceMonitor onChange={({ factor }) => setDpr(Math.round((DPR_MIN + (DPR_MAX - DPR_MIN) * factor) * 4) / 4)} />
        <CameraRig />
        {/* 우주 배경(starfield.tsx) — 인트로 동안은 꺼져 있고 REVEAL 에서 켜진다. 카메라가 돌면 시차로 별이 따라 돈다. 바닥색은 sticky 컨테이너 bg-black */}
        <Starfield />
        <Suspense fallback={null}>
          <GpuModel />
          <StatusLeds />
          <Smoke />
          <Studio />
          <Lights />
        </Suspense>
      </Canvas>
    </div>
  )
}
