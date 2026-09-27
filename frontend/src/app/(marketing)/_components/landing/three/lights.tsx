'use client'

import { Environment, Lightformer } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useRef } from 'react'
import { type Group, type Light, type SpotLight } from 'three'
import { P } from '../progress'

// 스윕 빔 세기 (P.beam 배율). INTRO TOP(sweep 0..1) 은 팬 위를 비스듬히 훑는 컷이라 카드가 어둡게 보여 더 밝게
const BEAM_TOP_MAIN = 18 // 30 → 18: 인트로 밝기 낮춤(2026-09-10)
const BEAM_TOP_AUX = 24 // 40 → 24
const BEAM_LOGO = 26 // HERO(sweep 2..3) 로고 스윕. 40 → 26
const LOGO_SWEEP_HZ = 0.22 // 조립 완료 뒤 좌우 왕복 속도(초당 왕복 수) — 한쪽 끝에서 반대쪽까지 약 2.3초
const LOGO_SWEEP_Z = 1.0 // 왕복 폭(±z). 끝 캡 폭보다 조금 넓게 지나가 빔 자국이 로고를 완전히 가로지름
// 조립 완료 뒤 조명은 "위에서 내려오는 스팟이 좌우로 훑으며 앞뒤로도 조금 움직이고, 비추는 지점도 카드 위를 돌아 각도가 계속 바뀌는" 느낌(2026-09-11 사용자 요청).
// 광원은 카드 위 높은 곳(y≈3), 타깃은 카드 윗면과 로고 캡 사이를 도는 별도 주기라 빔의 입사각이 계속 달라진다
const ROAM_HEIGHT = 2.4 // 광원 높이(로고 캡 앞 위쪽 — 캡 면과 윗면을 둘 다 비추는 각)
const ROAM_X = 0.4 // 광원 앞뒤(x) 흔들림 폭
const ROAM_HZ = 0.13 // 앞뒤·타깃 주기(좌우 왕복과 다른 주기 → 같은 궤적이 반복되지 않음)
const ROAM_INTENSITY = 90 // 물리 감쇠(거리²) 때문에 멀어진 만큼 세기를 올린다(로고 앞 1.3 거리의 26 과 비슷한 밝기)

// 조명: 스튜디오 조명은 전부 P.env 배율(챕터 1 암전 → 챕터 2 페이드인).
// 스윕 스팟 하나가 +X 끝 캡(로고)을 화면 왼→오(=+Z→−Z)로 훑는다. 살짝 위에서 비춰 metallic 상감의 반사각을 잡는다.
export function Lights() {
  const group = useRef<Group>(null)
  const spot = useRef<SpotLight>(null)
  const spot2 = useRef<SpotLight>(null) // 첫 컷 전용 보조 스팟: 메인과 같은 z, 카드 먼 쪽을 비춰 빔 자국을 세로(카드 길이)로 길게
  const top = useRef<Light>(null)
  const { scene } = useThree()
  useFrame(({ clock }) => {
    scene.environmentIntensity = P.env
    for (const o of group.current?.children ?? []) {
      const l = o as Light
      l.intensity = (l.userData.base ??= l.intensity) * P.env
    }
    if (top.current) top.current.intensity = P.topLight * 2.2
    const s = spot.current
    if (!s) return
    // 상감이 metallic 이라 하이라이트는 빛이 카메라 축 가까이 있을 때만 보인다 → 카메라(x≈2.35, y≈0.07) 근처에서 훑는다
    // 스윕 빔 경로(P.sweep). 첫 컷 카메라(+X 끝, −X 방향 응시)에서 화면 왼쪽 = +Z, 오른쪽 = −Z.
    //   0..1 카메라 쪽 낮은 위치에서 카드 폭 방향(+Z→−Z, 화면 왼→오)으로 이동 — 비스듬히 들어가 빔 자국이 카드 길이로 길게 늘어져 팬 3개를 한 번에 지나감
    //   1..2 캡 면(로고 스윕 시작점)으로 되돌아 내려옴(카메라 틸트다운과 동시) / 2..3 캡 앞에서 화면 왼→오(+Z→−Z)로 로고를 훑음
    const k = P.sweep
    const s2 = spot2.current
    if (k <= 1) {
      // 메인은 가까운 쪽(x 0.9), 보조는 먼 쪽(x −0.9)을 좁은 각으로 비춰 두 자국이 이어진 세로로 긴 얇은 띠가 된다(살짝만 보이게 어둡게)
      const z = 0.9 - 1.8 * k
      s.angle = 0.24
      // 메인은 카드 끝 모서리(x 1.45, y 0.1)를 노려 윗면 가까운 팬과 함께 끝 캡 정면 위쪽도 살짝 비춤
      s.position.set(2.9, 1.1, z)
      s.target.position.set(1.45, 0.22, z) // 조금 위로: 정면은 윗부분만 살짝
      if (s2) {
        s2.position.set(2.6, 1.25, z)
        s2.target.position.set(-0.9, 0.25, z)
        s2.intensity = P.beam * BEAM_TOP_AUX
        s2.target.updateMatrixWorld()
      }
    } else if (k <= 2) {
      const u = k - 1
      s.position.set(2.4 + 0.2 * u, 1.4 - 1.25 * u, -0.9 + 2.0 * u)
      s.target.position.set(0.2 + 1.35 * u, 0.25 - 0.23 * u, -0.9 + 0.9 * u)
    } else {
      s.position.set(2.6, 0.15, 1.1 - 2.2 * (k - 2))
      s.target.position.set(1.55, 0.02, 0)
    }
    if (k > 1) {
      s.angle = 0.4
      if (s2) s2.intensity = 0
    }
    s.intensity = P.beam * (k <= 1 ? BEAM_TOP_MAIN : BEAM_LOGO)
    // 조립 완료 뒤(P.logoSweep): 같은 스팟이 시간 기반으로 로고 앞을 좌우(+Z↔−Z)로 계속 왕복한다.
    // CAM_END(tilt 0.22, dist 4.5)의 카메라 축 근처에 두어 metallic 상감 하이라이트가 보이게 — 로고(1.55, 0.02, 0)에서 카메라 방향(0.95, 0.32)으로 1.3 만큼 떨어진 위치
    if (P.logoSweep > 0) {
      const t = clock.elapsedTime
      const z = LOGO_SWEEP_Z * Math.sin(t * Math.PI * 2 * LOGO_SWEEP_HZ) // 좌우 왕복(기존 속도·폭 유지)
      const w = t * Math.PI * 2 * ROAM_HZ
      s.angle = 0.5
      // 광원: 로고 캡 앞 위쪽에서 좌우로 훑고, 앞뒤(x)로도 천천히 오가며 살짝 오르내린다
      s.position.set(2.4 + ROAM_X * Math.sin(w), ROAM_HEIGHT + 0.3 * Math.cos(w * 0.7), z)
      // 타깃: 로고(1.55, 0.02) 와 카드 윗면 안쪽(0.9, 0.15) 사이를 오가고 z 도 광원과 다른 위상 → 입사각이 계속 바뀌며 캡과 윗면을 번갈아 비춘다
      const k2 = 0.5 + 0.5 * Math.sin(w + 0.9)
      s.target.position.set(1.55 - 0.65 * k2, 0.02 + 0.13 * k2, 0.5 * Math.sin(t * Math.PI * 2 * LOGO_SWEEP_HZ + 1.1))
      s.intensity = Math.max(s.intensity, P.logoSweep * ROAM_INTENSITY)
      if (s2) s2.intensity = 0
    }
    s.target.updateMatrixWorld()
  })
  return (
    <>
      <group ref={group}>
        <directionalLight position={[3, 6, 4]} intensity={1.6} />
        <directionalLight position={[-4, 2, -3]} intensity={0.6} color="#9ad1ff" />
        <directionalLight position={[2, -4, 3]} intensity={0.15} color="#d2d8cf" /> {/* 아래쪽 보조광: 거의 중성 — 그린이면 ORBIT 뒤 알루미늄 핀이 초록/올리브로 비침 */}
        <ambientLight intensity={0.15} />
      </group>
      <spotLight ref={spot} angle={0.4} penumbra={0.7} color="#eaffd0" intensity={0} />
      <spotLight ref={spot2} angle={0.13} penumbra={0.8} color="#eaffd0" intensity={0} />
      {/* 탑뷰 전용: 바로 위에서 내려 비추는 조명(P.topLight) — PCB 부속이 잘 보이게 */}
      <directionalLight ref={top} position={[0.3, 8, 1.5]} intensity={0} color="#ffffff" />
    </>
  )
}

/** 스튜디오 환경맵: 외부 HDR 없이 Lightformer 로 생성 → 금속 반사 */
export function Studio() {
  return (
    <Environment resolution={256} frames={1}>
      <Lightformer form="rect" intensity={6} position={[0, 5, 0]} scale={[8, 3, 1]} rotation-x={Math.PI / 2} />
      <Lightformer form="rect" intensity={3} position={[-5, 2, 3]} scale={[3, 6, 1]} rotation-y={Math.PI / 3} />
      <Lightformer form="rect" intensity={2.5} position={[5, 1, -3]} scale={[3, 5, 1]} rotation-y={-Math.PI / 2.5} />
    </Environment>
  )
}
