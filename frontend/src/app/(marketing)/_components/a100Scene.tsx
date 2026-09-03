'use client'

import { createScope, createTimeline, onScroll, utils } from 'animejs'
import { useEffect, useRef } from 'react'
import { A100Rig, CAMERA } from './a100Rig'

// Step 2: 스크롤 = 카메라 이동. 상판 정면(top) → 등각 뷰(iso). 섹션 높이 220vh, sticky 캔버스.
export function A100Scene() {
  const root = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!root.current) return
    const scope = createScope({ root: root.current }).add(() => {
      utils.set('#rig', CAMERA.top)
      createTimeline({
        defaults: { ease: 'inOutSine', duration: 1000 },
        autoplay: onScroll({
          target: root.current!,
          enter: { container: 'top', target: 'top' },
          leave: { container: 'bottom', target: 'bottom' },
          sync: true,
        }),
      }).add('#rig', { rotateX: CAMERA.iso.rotateX, rotateZ: CAMERA.iso.rotateZ, scale: CAMERA.iso.scale }, 0)
    })
    return () => scope.revert()
  }, [])

  return (
    <section ref={root} className="relative h-[220vh] bg-black">
      <div className="sticky top-0 flex h-screen items-center justify-center overflow-hidden">
        <div style={{ perspective: 1400 }}>
          <div className="scale-[0.55] sm:scale-75 lg:scale-100">
            <A100Rig camera="top" />
          </div>
        </div>
      </div>
    </section>
  )
}
