'use client'

import { Stars } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useRef } from 'react'
import type { Points } from 'three'
import { P } from '../progress'

// 우주 배경 별. 인트로(암전·빔 스윕) 동안은 순수 검정이어야 하므로 P.stars 가 0 이면 그리지 않는다 — REVEAL 에서 조명이 켜질 때 함께 켜진다(chapters.ts).
// drei Stars 는 자체 셰이더(조명 P.env 무관)라 visible 로만 끈다. 점 크기 = factor × (30 / 거리) → 반경 30~70, factor 8 로 3~8px. 카메라 far(200) 안에 있어야 보인다
export function Starfield() {
  const ref = useRef<Points>(null)
  useFrame(() => {
    if (ref.current) ref.current.visible = P.stars > 0
  })
  return <Stars ref={ref} radius={30} depth={40} count={440} factor={8} saturation={0} fade speed={0.5} />
}
