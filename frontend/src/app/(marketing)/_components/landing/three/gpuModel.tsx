'use client'

import { useGLTF } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useMemo } from 'react'
import { type Mesh, MeshStandardMaterial, Object3D, Vector3 } from 'three'
import { P } from '../progress'

const MODEL = '/models/gpu.glb'

type Part = {
  obj: Object3D
  base: Vector3 // 조립 위치
  ex: Vector3 // Blender explode 벡터(glTF 좌표) — 스택 배치에 사용
  core: number // 0 히트파이프, 1 콜드플레이트·브릿지, 아니면 -1
  choke: number // VRM 초크면 위상(A 조 0, B 조 π), 아니면 -1 — 챕터 9 이후 두 조가 번갈아 높이가 줄었다 돌아옴
  spin: boolean // 팬(회전)
  hs: boolean // 히트싱크(핀·히트파이프) — 챕터 7~8 에서 함께 들리고 위로 나감
}

const FAN_LIFT = 4 // 챕터 3: 팬 상승 거리(화면 밖)
const SHROUD_LIFT = 4.5 // 챕터 5: 쉬라우드가 화면 밖까지 올라가는 높이(glTF 단위)
const HS_LIFT = 0.5 // 챕터 7: 히트싱크(핀+히트파이프)가 들리는 높이
const PLATE_LIFT = 0.2 // 챕터 7: 콜드플레이트·브릿지가 들리는 높이(히트싱크보다 낮아 층이 구분됨)
const HS_OFF = 7 // 챕터 8: 함께 위로 화면 밖까지 상승하는 거리(다 나간 뒤에만 숨김)
const CHOKE_SQUASH = 0.35 // 초크 높이가 줄어드는 최대 비율(1 = 완전히 납작)
const CHOKE_SPEED = 4 // 초크 펌핑 속도(rad/s)
const CHOKE_H = 0.09 // 초크 높이(Blender box 0.09) — 바닥을 PCB 에 붙여 두기 위한 보정용
const CHOKE_A = new Set([1, 8, 10, 17, 3]) // A 조. 나머지 초크(11, 2, 9, 0, 18, 16)는 B 조로 반대 위상
const STACK_SCALE = 1.0 // 챕터 12: Blender explode 벡터 배수(층 간격)
const STACK_FIN_Y = 0.55 // 챕터 12: 핀+히트파이프 층 높이(explode 단위). 둘 다 조립 상태 그대로 함께 떠오름
const STACK_TOP_Y = 0.95 // 챕터 12: 쉬라우드+팬 층 높이(explode 단위). 팬은 쉬라우드에 끼운 채 함께 떠오름
const stackTarget = new Vector3()

// 부품 종류별 재질 오버라이드(GLB/Blender 는 그대로). 이름 접두 → 재질
const LOOKS: [RegExp, MeshStandardMaterial][] = [
  [/^Backplate$/, new MeshStandardMaterial({ color: '#8f9296', metalness: 1, roughness: 0.32 })], // 스틸
  [/^Choke_/, new MeshStandardMaterial({ color: '#2b2f33', metalness: 0.35, roughness: 0.75 })], // 페라이트 초크: 무광 차콜
  [/^Cap_/, new MeshStandardMaterial({ color: '#1c2a3f', metalness: 0.55, roughness: 0.35 })], // 폴리머 캡: 짙은 네이비 알루미늄 캔
  [/^Mosfet_/, new MeshStandardMaterial({ color: '#0b0c0e', metalness: 0.15, roughness: 0.45 })], // 모스펫: 검은 에폭시
  [/^VRAM_/, new MeshStandardMaterial({ color: '#141a1d', metalness: 0.25, roughness: 0.28 })], // GDDR: 약간 광택 있는 다크 패키지
  [/^SMD/, new MeshStandardMaterial({ color: '#5c5348', metalness: 0.6, roughness: 0.5 })], // SMD(저항열·디커플링·VRAM 옆 저항): 탄탈/세라믹 브라운
  [/_IC$/, new MeshStandardMaterial({ color: '#0a0b0c', metalness: 0.2, roughness: 0.42 })], // PWM/BIOS IC: 검은 몰딩
]

export function GpuModel() {
  const { scene } = useGLTF(MODEL, true) // Draco 디코더는 drei 기본(구글 CDN) 사용

  const parts = useMemo<Part[]>(() => {
    const list: Part[] = []
    scene.traverse((o) => {
      const ex = o.userData.explode as number[] | undefined
      if (!ex) return // Blender tag() 로 커스텀 프로퍼티가 붙은 것만 '부품'
      const look = LOOKS.find(([re]) => re.test(o.name))?.[1]
      if (look) o.traverse((c) => { if ((c as Mesh).isMesh) (c as Mesh).material = look })
      const core = o.name.startsWith('Heatpipe_') ? 0 : o.name === 'ColdPlate' || o.name.startsWith('PlateBridge_') ? 1 : -1
      const choke = o.name.startsWith('Choke_') ? (CHOKE_A.has(Number(o.name.slice(6))) ? 0 : Math.PI) : -1
      list.push({ obj: o, base: o.position.clone(), ex: new Vector3(ex[0], ex[2], -ex[1]), core, choke, spin: o.userData.spin === 1, hs: o.name.startsWith('Fin_') || core === 0 }) // Blender(x,y,z) → glTF(x,z,-y)
    })
    return list
  }, [scene])

  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime
    for (const p of parts) {
      // 매 프레임 조립 위치에서 시작해 챕터별 오프셋을 더한다(스크롤 되감기 가능)
      // 챕터 3: 팬 3개가 동시에 위로 상승해 화면 밖으로(시차 없음 — 시차를 두면 스크롤이 조금만 움직여도 한 팬만 먼저 떠 보인다). 챕터 5: 쉬라우드가 분리되어 위로
      const up = p.spin ? P.fans * FAN_LIFT : p.obj.name === 'Shroud' ? P.shroud * SHROUD_LIFT : 0
      p.obj.position.set(p.base.x, p.base.y + up, p.base.z)
      // 다 올라간 팬(fans=1)·쉬라우드(shroud=1)는 숨김 — 뒤 챕터에서 카메라가 위로 올라가도 안 보이게. 되감으면 다시 보임
      let hidden = p.spin ? P.fans >= 1 : p.obj.name === 'Shroud' ? P.shroud >= 1 : false
      if (p.hs || p.core === 1) {
        // 챕터 7: 히트싱크는 HS_LIFT, 콜드플레이트·브릿지는 PLATE_LIFT 만큼 들려 층이 구분됨. 챕터 8: 함께 위로 상승해 화면 밖으로, 다 나간 뒤 숨김
        p.obj.position.y += P.hsLift * (p.hs ? HS_LIFT : PLATE_LIFT) + P.hsOff * HS_OFF
        if (P.hsOff >= 1) hidden = true
      }
      // 챕터 7 부터(히트싱크가 들려 기판이 드러날 때): VRM 초크들의 높이(Y 스케일)가 계속 줄었다 돌아옴 — A/B 두 조가 번갈아(반대 위상). 바닥은 PCB 에 붙어 있게 중심을 내려 보정
      if (p.choke >= 0) {
        const sy = 1 - Math.min(P.hsLift * 2, 1) * CHOKE_SQUASH * (0.5 - 0.5 * Math.cos(t * CHOKE_SPEED + p.choke))
        p.obj.scale.y = sy
        p.obj.position.y -= (1 - sy) * CHOKE_H * 0.5
      }
      // 챕터 10: 햄버거 레시피 구조도 — 냉각 스택(팬·쉬라우드·핀·히트파이프·플레이트류)만 Blender explode 배치 × STACK_SCALE 로 층층이,
      // 브라켓·PCB·백플레이트와 그 위 부속(VRAM·초크·캡·모스펫·핀헤더)은 조립 상태로 붙어 있음. 진행도로 현재 위치에서 보간
      // 챕터 11(assemble): 층 간격이 0 으로 줄어 조립 완료
      if (P.stack > 0) {
        const gap = STACK_SCALE * (1 - P.assemble)
        stackTarget.copy(p.base)
        if (p.hs) stackTarget.y += STACK_FIN_Y * gap // 핀·히트파이프: 조립 상태 그대로 한 층으로 함께 떠오름
        else if (p.spin || p.obj.name === 'Shroud') stackTarget.y += STACK_TOP_Y * gap // 쉬라우드+팬: 조립 상태 그대로 한 층
        else if (p.core === 1) stackTarget.addScaledVector(p.ex, gap) // 콜드플레이트·브릿지: explode 배치
        p.obj.position.lerp(stackTarget, P.stack)
        hidden = false
      }
      p.obj.visible = !hidden
      if (p.spin) p.obj.rotation.y -= dt * 5 * P.spin // 블레이드 앞전 방향(시계 방향, 위에서 볼 때)
    }
  })

  return <primitive object={scene} />
}

useGLTF.preload(MODEL, true)
