'use client'

import { useState } from 'react'

// Layer × Module heatmap — 학습 전후 LoRA weight 변화량(layerModuleDeltaMagnitude)을 색으로.
// 색만으로 말하지 않는다: 셀은 버튼이라 hover(title)·클릭/탭·키보드 포커스로 숫자를 아래 한 줄에 보여 준다.
// 좁은 화면은 축소 대신 가로 스크롤(overflow-x-auto)

/** 표시 순서 고정 — 짧은 표기 → 실제 module 이름 */
const MODULES: [short: string, full: string][] = [
  ['q', 'q_proj'],
  ['k', 'k_proj'],
  ['v', 'v_proj'],
  ['o', 'o_proj'],
  ['gate', 'gate_proj'],
  ['up', 'up_proj'],
  ['down', 'down_proj'],
]

type Cell = { layer: string; module: string; value: number }

export function LoraWeightHeatmap({ data }: { data: Record<string, Record<string, number>> }) {
  const [selected, setSelected] = useState<Cell | null>(null)

  const layers = Object.keys(data).sort((x, y) => Number(x) - Number(y))
  if (layers.length === 0) return <p className="text-caption text-text-muted">Layer별 weight 변화 데이터가 제공되지 않습니다.</p>

  // 전체 셀 min/max 로 0~1 정규화 — MVP 라 값 왜곡 없는 선형만(극단값이 문제되면 log/percentile 검토)
  const values = layers.flatMap((l) => MODULES.map(([, full]) => data[l]?.[full]).filter((v): v is number => v != null))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto pb-1">
        <table className="border-separate" style={{ borderSpacing: 3 }}>
          <thead>
            <tr>
              <td />
              {MODULES.map(([short, full]) => (
                <th key={full} title={full} className="min-w-[36px] pb-1 text-center text-micro font-medium text-text-muted">
                  {short}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {layers.map((layer) => (
              <tr key={layer}>
                <th scope="row" className="pr-2 text-right text-micro font-medium tabular-nums text-text-muted">
                  L{layer}
                </th>
                {MODULES.map(([, full]) => {
                  const v = data[layer]?.[full]
                  if (v == null)
                    return (
                      <td key={full}>
                        <div className="h-5 w-9 rounded-[3px] bg-surface-1" title={`Layer ${layer} · ${full} — 데이터 없음`} />
                      </td>
                    )
                  const t = (v - min) / span
                  const on = selected?.layer === layer && selected?.module === full
                  return (
                    <td key={full}>
                      <button
                        type="button"
                        title={`Layer ${layer}\n${full}\nWeight delta: ${v}`}
                        aria-label={`Layer ${layer} ${full} weight delta ${v}`}
                        onClick={() => setSelected({ layer, module: full, value: v })}
                        className={`block h-5 w-9 rounded-[3px] transition-shadow ${on ? 'ring-2 ring-dark' : 'focus-visible:ring-2 focus-visible:ring-primary'}`}
                        style={{ backgroundColor: `color-mix(in srgb, var(--color-primary) ${Math.round(8 + 88 * t)}%, transparent)` }}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* 선택 셀 상세 — 모바일 tap 도 이 줄로 확인한다 */}
      <p className="min-h-[20px] text-caption tabular-nums text-text-muted" aria-live="polite">
        {selected ? (
          <>
            Layer {selected.layer} · <span className="font-medium text-dark">{selected.module}</span> · Weight delta {selected.value}
          </>
        ) : (
          '셀을 누르면 정확한 변화량을 보여 줍니다. 진할수록 많이 학습된 부분입니다.'
        )}
      </p>
    </div>
  )
}
