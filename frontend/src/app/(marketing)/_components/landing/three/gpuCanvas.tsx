'use client'

import { Canvas } from '@react-three/fiber'
import { Suspense, useEffect, useRef, useState } from 'react'
import { ACESFilmicToneMapping } from 'three'
import { CameraRig } from './cameraRig'
import { GpuModel } from './gpuModel'
import { Lights, Studio } from './lights'
import { Smoke } from './smoke'
import { StatusLeds } from './statusLeds'

// r3f 캔버스 조립. 카메라는 cameraRig, 조명은 lights, 부품은 gpuModel — 모두 매 프레임 P 를 읽는다.
// 최적화: (1) 스테이지가 뷰포트 밖이면 frameloop 을 멈춰 Hero·Footer 를 보는 동안 GPU/CPU 를 쓰지 않는다
//        (2) three/drei 청크(약 1MB)와 GLB 로드는 브라우저가 한가해진 뒤 시작해 Hero 첫 화면과 경쟁하지 않는다(최대 1.5초 지연)
export function GpuCanvas() {
  const host = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const el = host.current
    if (!el) return
    const hasIdle = typeof window.requestIdleCallback === 'function'
    const idle = hasIdle ? window.requestIdleCallback(() => setReady(true), { timeout: 1500 }) : window.setTimeout(() => setReady(true), 200)
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { rootMargin: '25%' }) // 조금 일찍 깨워 진입 시 첫 프레임이 준비돼 있게
    io.observe(el)
    return () => {
      io.disconnect()
      if (hasIdle) window.cancelIdleCallback(idle)
      else window.clearTimeout(idle)
    }
  }, [])
  return (
    <div ref={host} className="h-full w-full">
      {ready && (
        <Canvas
          frameloop={visible ? 'always' : 'never'}
          dpr={[1, 1.75]}
          camera={{ fov: 32, near: 0.1, far: 50, position: [3, 2.5, 4] }}
          gl={{ antialias: true, toneMapping: ACESFilmicToneMapping, toneMappingExposure: 1.1 }}
          style={{ background: 'transparent' }} // 배경색은 뒤의 sticky 컨테이너(bg-dark)와 INTRO TOP 전용 #deep 레이어가 담당
        >
          <CameraRig />
          <Suspense fallback={null}>
            <GpuModel />
            <StatusLeds />
            <Smoke />
            <Studio />
            <Lights />
          </Suspense>
        </Canvas>
      )}
    </div>
  )
}
