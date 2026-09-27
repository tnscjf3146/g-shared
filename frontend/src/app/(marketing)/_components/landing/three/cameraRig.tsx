'use client'

import { useFrame } from '@react-three/fiber'
import { P } from '../progress'

// 카메라: P(yaw/tilt/dist/lookY) 구면 좌표. 매 프레임 P 를 읽는다. (마우스 시차 없음 — 카드 고정)
export function CameraRig() {
  useFrame(({ camera }) => {
    const yaw = P.yaw
    const tilt = P.tilt
    const c = Math.cos(tilt)
    // 타깃을 카메라 오른쪽 방향의 반대로 밀면 카드가 화면 오른쪽으로 간다
    const tx = -Math.cos(yaw) * P.shift
    const tz = Math.sin(yaw) * P.shift
    camera.position.set(tx + Math.sin(yaw) * c * P.dist, P.lookY + Math.sin(tilt) * P.dist, tz + Math.cos(yaw) * c * P.dist)
    camera.lookAt(tx, P.lookY, tz)
  })
  return null
}
