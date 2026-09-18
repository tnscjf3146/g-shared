'use client'

import { useThree } from '@react-three/fiber'
import { useEffect } from 'react'
import { GPU_READY_EVENT } from '../progress'

// 셰이더 사전 컴파일 — 씬이 다 모인 뒤, 첫 프레임을 그리기 전에(2026-09-17).
//
// 재질 프로그램은 그 재질이 처음 그려지는 프레임에 컴파일된다. 부품이 많아 첫 렌더에 몰리면 1초 넘게 멈춘다(측정 1163ms).
// compileAsync 는 KHR_parallel_shader_compile 로 드라이버가 뒤에서 컴파일하게 하고, 전 프로그램이 링크됐을 때 resolve 한다.
//
// **Suspense 의 마지막 자식**이어야 한다. 프로그램 정의는 씬의 조명 개수와 environment 유무로 정해지는데, React 는 형제 effect 를
// 순서대로 돌리므로 여기 effect 는 Studio(환경맵)·Lights 가 붙은 뒤에 실행된다. GpuModel 안에서 컴파일했을 때는 그 둘이 아직
// 없어서 정의가 다른 프로그램을 만들었고, 첫 프레임에 환경맵·조명이 붙자 three 가 전부 다시 컴파일했다(남아 있던 383ms).
//
// 끝나면 GPU_READY_EVENT 를 쏜다. gpuCanvas 는 이 신호까지 frameloop 을 끄고 있어 링크가 안 끝난 프로그램을 쓰려다
// 동기로 기다리는 프레임이 없고, landingScene 은 이 신호에 인트로를 시작한다. 실패해도 신호는 보낸다 — 컴파일은 첫 렌더가 어차피 한다
export function Precompile() {
  const { gl, camera, scene } = useThree()
  useEffect(() => {
    let alive = true
    const announce = () => {
      if (alive) window.dispatchEvent(new Event(GPU_READY_EVENT))
    }
    const pending = gl.compileAsync?.(scene, camera)
    if (pending) pending.then(announce, announce)
    else announce()
    return () => {
      alive = false
    }
  }, [gl, camera, scene])
  return null
}
