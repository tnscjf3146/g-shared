'use client'

import { Canvas } from '@react-three/fiber'
import { Suspense } from 'react'
import { ACESFilmicToneMapping } from 'three'
import { CameraRig } from './cameraRig'
import { GpuModel } from './gpuModel'
import { Lights, Studio } from './lights'
import { Smoke } from './smoke'
import { StatusLeds } from './statusLeds'

// r3f 캔버스 조립. 카메라는 cameraRig, 조명은 lights, 부품은 gpuModel — 모두 매 프레임 P 를 읽는다.
export function GpuCanvas() {
  return (
    <Canvas
      dpr={[1, 1.75]}
      camera={{ fov: 32, near: 0.1, far: 50, position: [3, 2.5, 4] }}
      gl={{ antialias: true, toneMapping: ACESFilmicToneMapping, toneMappingExposure: 1.1 }}
      style={{ background: '#000' }}
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
  )
}
