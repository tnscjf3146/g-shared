'use client'

import { useFrame } from '@react-three/fiber'
import { engine } from 'animejs'
import { useEffect } from 'react'

// anime.js 의 자체 rAF 루프를 끄고 r3f 프레임 루프 안에서 직접 돌린다(2026-09-17).
//
// 왜: 타임라인이 P(progress.ts)를 쓰고 씬 컴포넌트들이 같은 P 를 읽는데, 두 루프가 따로 돌면
// 한 프레임 안에서 어느 쪽이 먼저 실행될지가 보장되지 않는다. 순서가 뒤바뀌는 프레임에서는 씬이
// 지난 프레임 값을 읽어 한 프레임만큼 뒤처지고, 그 뒤처짐이 들쭉날쭉하면 미세한 떨림으로 보인다.
// 측정에서도 스크롤 중 프레임당 rAF 콜백이 2.7 개였다(정지 시 1 개) → 통합 뒤 1.7 개.
//
// 이 컴포넌트는 **Canvas 의 첫 자식**이어야 한다 — r3f 는 같은 우선순위(0)의 useFrame 을 구독 순서대로
// 부르므로, 카메라·모델·조명이 P 를 읽기 전에 타임라인이 이번 프레임 값으로 갱신된다.
//
// active 는 "r3f 루프가 실제로 도는가"(gpuCanvas 의 frameloop 조건과 같은 값)여야 한다. 꺼져 있는 동안은
// anime 에 자기 루프를 돌려준다 — 안 그러면 캔버스가 멈춘 동안 DOM 쪽 타임라인(chapterCopy 등)까지 같이 멈춘다.
//
// 주의(anime 4.5 engine.js): tickEngine 은 useDefaultMainLoop 을 확인하지 않는다. 플래그는 wake() 가 루프를
// **새로 시작할지**만 정하고, 이미 돌고 있는 루프는 자식이 없어질 때까지 계속 돈다. 그래서 수동 모드로 들어갈 때는
// 예약된 rAF 를 직접 취소해야 두 루프가 겹치지 않는다(챕터 애니메이션 도중 캔버스가 화면을 나갔다 돌아오는 경우).
export function AnimeTick({ active }: { active: boolean }) {
  useEffect(() => {
    if (active) {
      engine.useDefaultMainLoop = false
      // reqId 타입은 number | NodeJS.Immediate(Node 폴백). 브라우저에서는 rAF 번호라 숫자일 때만 취소한다 — 단언 대신 가드
      if (typeof engine.reqId === 'number' && engine.reqId) {
        cancelAnimationFrame(engine.reqId)
        engine.reqId = 0
      }
    } else {
      engine.useDefaultMainLoop = true
      engine.wake() // 자식이 있을 때만 실제로 돈다(tickEngine 이 _head 를 본다)
    }
    return () => {
      engine.useDefaultMainLoop = true
      engine.wake()
    }
  }, [active])
  useFrame(() => {
    if (active) engine.update()
  })
  return null
}
