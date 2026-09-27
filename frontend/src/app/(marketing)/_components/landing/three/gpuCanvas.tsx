'use client'

import { Canvas } from '@react-three/fiber'
import { Suspense, useEffect, useRef, useState } from 'react'
import { ACESFilmicToneMapping } from 'three'
import { GPU_READY_EVENT, GPU_READY_TIMEOUT_MS } from '../progress'
import { AnimeTick } from './animeTick'
import { Backdrop } from './backdrop'
import { CameraRig } from './cameraRig'
import { ChainCallout } from './chainCallout'
import { EscrowCallout } from './escrowCallout'
import { FinCallout } from './finCallout'
import { GpuFitCallout } from './gpuFitCallout'
import { GpuModel } from './gpuModel'
import { Precompile } from './precompile'
import { ProviderCallout } from './providerCallout'
import { Lights, Studio } from './lights'
import { StatusLeds } from './statusLeds'

// r3f 캔버스 조립. 카메라는 cameraRig, 조명은 lights, 부품은 gpuModel — 모두 매 프레임 P 를 읽는다.
// 최적화: 스테이지가 뷰포트 밖이면 frameloop 을 멈춰 Footer 를 보는 동안 GPU/CPU 를 쓰지 않는다.
// 로드 중에도 멈춰 둔다(ready): 셰이더 사전 컴파일(precompile.tsx)이 끝나기 전에 그리면 링크 안 된 프로그램을 쓰려다 동기로 기다린다.
// 인트로 첫 컷이 암전이라 그동안 안 그려도 보이는 차이가 없다
// 해상도 배율은 **고정**이다(2026-09-17 측정 결과). 적응형을 두 번 시도했다가 둘 다 걷어냈다.
//  1) PerformanceMonitor(fps 기준): 로드 중 한 번 생긴 1163ms 멈춤을 보고 dpr 을 1.5 → 1.25 로 내린 뒤 다시 올리지 않아, 그 뒤로 내내 흐린 화면이 남았다.
//  2) 스크롤 중에만 1.0 으로 낮추기: 픽셀 수를 56% 줄여도 프레임이 전혀 나아지지 않았다(55.3 → 54.9fps, 노이즈 범위).
//     이 장면은 래스터 처리량이 병목이 아니라는 뜻이다. 프레임 이득 없이 스크롤할 때마다 배경 선명도만 눈에 띄게 바뀌었다.
// 그래서 하나로 고정한다 — 항상 같은 선명도, 코드도 제일 적다. 훨씬 약한 GPU 에서 문제가 보고되면 그때 다시 본다
const DPR = 1.5
// (Hero 가 없어진 뒤로 캔버스가 첫 화면이라 로드를 미루지 않는다 — 인트로는 GpuModel 의 GPU_READY_EVENT 를 기다린다)
export function GpuCanvas() {
  const host = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [ready, setReady] = useState(false) // precompile.tsx 의 GPU_READY_EVENT, 또는 타임아웃(landingScene 의 인트로 시작과 같은 값)
  useEffect(() => {
    const on = () => setReady(true)
    addEventListener(GPU_READY_EVENT, on)
    const t = setTimeout(on, GPU_READY_TIMEOUT_MS)
    return () => {
      removeEventListener(GPU_READY_EVENT, on)
      clearTimeout(t)
    }
  }, [])
  const running = visible && ready
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
        frameloop={running ? 'always' : 'never'}
        dpr={DPR}
        camera={{ fov: 32, near: 0.1, far: 200, position: [3, 2.5, 4] }}
        gl={{
          antialias: true,
          toneMapping: ACESFilmicToneMapping,
          toneMappingExposure: 1.1,
        }}
        style={{ background: 'transparent' }} // 배경(backdrop.tsx, 전부 스크롤 스크럽): 01 구간에 밝아지는 차콜 금속판 → 03 검정이 아래서 위로 덮음 → 04 부터 끝까지 조명이 켜지며 맨들한 스틸판. 뒤 sticky 컨테이너는 검정
      >
        {/* 타임라인 갱신을 가장 먼저 — 아래 컴포넌트들이 이번 프레임 값의 P 를 읽게 된다(animeTick.tsx). active 는 frameloop 조건과 같은 값 */}
        <AnimeTick active={running} />
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
          {/* 반드시 마지막 — 조명·환경맵이 붙은 뒤에 컴파일해야 첫 프레임에 다시 컴파일하지 않는다(precompile.tsx) */}
          <Precompile />
        </Suspense>
      </Canvas>
    </div>
  )
}
