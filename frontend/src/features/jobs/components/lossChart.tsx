'use client'

import { useRef, useState } from 'react'
import type { LossPoint } from '../types'

// Loss Curve — 발표용 MVP 라 차트 라이브러리 없이 SVG 직접(프로젝트에 차트 의존성이 없다).
// hover/tap 으로 가장 가까운 점의 "Step n / Loss x" 를 보여 준다. 기록이 없으면 차트 영역만 빈 상태(패널은 유지)

const W = 320
const H = 170
const PAD = { top: 14, right: 12, bottom: 26, left: 40 }

export function LossChart({ history }: { history: LossPoint[] }) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [active, setActive] = useState<number | null>(null)

  if (history.length === 0) {
    return (
      <div className="flex h-[170px] items-center justify-center">
        <p className="text-[14px] text-text-muted">Step별 Loss 기록이 제공되지 않습니다.</p>
      </div>
    )
  }

  const xs = history.map((p) => p.step)
  const ys = history.map((p) => p.loss)
  const xMin = Math.min(...xs)
  const xMax = Math.max(...xs)
  const yMin = Math.min(...ys)
  const yMax = Math.max(...ys)
  const xSpan = xMax - xMin || 1 // 점이 1개면 가운데에 찍는다
  const ySpan = yMax - yMin || 1
  const px = (step: number) => PAD.left + ((step - xMin) / xSpan) * (W - PAD.left - PAD.right)
  const py = (loss: number) => PAD.top + (1 - (loss - yMin) / ySpan) * (H - PAD.top - PAD.bottom)
  const pts = history.map((p) => ({ ...p, x: px(p.step), y: py(p.loss) }))
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')

  // 포인터 x 좌표에서 가장 가까운 점 — viewBox 좌표로 환산해 찾는다
  const pick = (clientX: number) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return
    const vx = ((clientX - rect.left) / rect.width) * W
    let best = 0
    pts.forEach((p, i) => {
      if (Math.abs(p.x - vx) < Math.abs(pts[best].x - vx)) best = i
    })
    setActive(best)
  }
  const a = active != null ? pts[active] : null
  // 라벨이 차트 밖으로 안 나가게 좌우로 붙인다
  const labelX = a ? Math.min(Math.max(a.x, PAD.left + 34), W - PAD.right - 34) : 0

  return (
    <figure className="flex flex-col gap-1">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Loss curve — step ${xMin}에서 ${yMax.toFixed(3)}, step ${xMax}에서 ${history[history.length - 1].loss.toFixed(3)}`}
        className="w-full touch-none select-none"
        onPointerMove={(e) => pick(e.clientX)}
        onPointerDown={(e) => pick(e.clientX)}
        onPointerLeave={() => setActive(null)}
      >
        {/* 옅은 가로 그리드 3줄 + 축 라벨 */}
        {[0, 0.5, 1].map((t) => {
          const y = PAD.top + t * (H - PAD.top - PAD.bottom)
          return <line key={t} x1={PAD.left} x2={W - PAD.right} y1={y} y2={y} stroke="var(--color-hairline)" strokeWidth="1" />
        })}
        <text x={PAD.left - 6} y={PAD.top + 4} textAnchor="end" fontSize="9" fill="var(--color-text-muted)" className="tabular-nums">
          {yMax.toFixed(2)}
        </text>
        <text x={PAD.left - 6} y={H - PAD.bottom + 3} textAnchor="end" fontSize="9" fill="var(--color-text-muted)" className="tabular-nums">
          {yMin.toFixed(2)}
        </text>
        <text x={PAD.left - 26} y={(PAD.top + H - PAD.bottom) / 2} fontSize="9" fill="var(--color-text-muted)" transform={`rotate(-90 ${PAD.left - 26} ${(PAD.top + H - PAD.bottom) / 2})`} textAnchor="middle">
          Loss
        </text>
        <text x={(PAD.left + W - PAD.right) / 2} y={H - 4} textAnchor="middle" fontSize="9" fill="var(--color-text-muted)">
          Training Step
        </text>

        <path d={path} fill="none" stroke="var(--color-primary)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {pts.map((p, i) => (
          <circle key={p.step} cx={p.x} cy={p.y} r={i === active ? 4 : 2.5} fill={i === active ? 'var(--color-primary-dark)' : 'var(--color-primary)'}>
            <title>{`Step ${p.step}\nLoss ${p.loss}`}</title>
          </circle>
        ))}
        {a && (
          <g pointerEvents="none">
            <line x1={a.x} x2={a.x} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--color-border)" strokeWidth="1" strokeDasharray="3 3" />
            <rect x={labelX - 34} y={PAD.top - 12} width="68" height="24" rx="4" fill="var(--color-black-soft)" opacity="0.92" />
            <text x={labelX} y={PAD.top - 2} textAnchor="middle" fontSize="8.5" fill="#fff" className="tabular-nums">
              Step {a.step}
            </text>
            <text x={labelX} y={PAD.top + 8} textAnchor="middle" fontSize="8.5" fill="#fff" className="tabular-nums">
              Loss {a.loss}
            </text>
          </g>
        )}
      </svg>
      <figcaption className="text-[12px] tabular-nums text-text-muted">
        Step {xMin} → {xMax} · Loss {ys[0]} → {ys[ys.length - 1]}
      </figcaption>
    </figure>
  )
}
