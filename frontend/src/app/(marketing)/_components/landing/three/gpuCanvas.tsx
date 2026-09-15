'use client'

import { PerformanceMonitor } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { Suspense, useEffect, useRef, useState } from 'react'
import { ACESFilmicToneMapping } from 'three'
import { Backdrop } from './backdrop'
import { CameraRig } from './cameraRig'
import { ChainCallout } from './chainCallout'
import { EscrowCallout } from './escrowCallout'
import { FinCallout } from './finCallout'
import { GpuFitCallout } from './gpuFitCallout'
import { GpuModel } from './gpuModel'
import { ProviderCallout } from './providerCallout'
import { Lights, Studio } from './lights'
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
        camera={{ fov: 32, near: 0.1, far: 200, position: [3, 2.5, 4] }}
        gl={{
          antialias: true,
          toneMapping: ACESFilmicToneMapping,
          toneMappingExposure: 1.1,
        }}
        style={{ background: 'transparent' }} // 배경(backdrop.tsx, 전부 스크롤 스크럽): 01 구간에 밝아지는 차콜 금속판 → 03 검정이 아래서 위로 덮음 → 04 부터 끝까지 조명이 켜지며 맨들한 스틸판. 뒤 sticky 컨테이너는 검정
      >
        {/* fps 가 기준 아래로 떨어지면 factor 가 줄어든다 → dpr 을 1..1.5 사이로 */}
        <PerformanceMonitor onChange={({ factor }) => setDpr(Math.round((DPR_MIN + (DPR_MAX - DPR_MIN) * factor) * 4) / 4)} />
        <CameraRig />
        <Suspense fallback={null}>
          {/* 배경은 차콜판 한 장 고정(로고 정중앙, 스크롤 무관) — 2026-09-14 사용자 결정. 검정판 와이프·스틸판 조명은 backdrop.tsx 에 남겨둔 채 마운트만 안 한다 */}
          <Backdrop kind="shroud" />
          <GpuModel />
          {/* 지시선 콜아웃(three/callout.tsx 공용) — GpuModel 뒤에 두어 Fin_ 병합이 끝난 scene 을 본다.
              항목 하나 = 선 하나 = 부품 하나(animejs.com 식). 02 핀 네 지점 → 할 수 있는 일 / 03 긴 변 → GPU 등급·AI 추천 / 04 PWM·전원·LED·BIOS·다이 → 수익·워커 / 05 콜드플레이트 → 에스크로 장부(표라 하나) / 06 층마다 → 블록 기록, 기판 끝 → tx */}
          <FinCallout />
          <GpuFitCallout />
          <ProviderCallout />
          <EscrowCallout />
          <ChainCallout />
          <StatusLeds />
          {/* 연기(three/smoke.tsx)는 02 재생 1.8초 동안 GPU fill 이 커서 뺐다(2026-09-15 사용자 결정, A/B 확인용). 되살리려면 import { Smoke } from './smoke' 와 <Smoke /> 두 줄 */}
          <Studio />
          <Lights />
        </Suspense>
      </Canvas>
    </div>
  )
}
