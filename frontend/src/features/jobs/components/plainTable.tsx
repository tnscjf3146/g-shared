import type { ReactNode } from 'react'

// 장식 없는 격자 표 한 줄: 헤더 행(라벨) + 값 행. 모든 칸 테두리, 회색 헤더, 각진 모서리, 컬럼 폭 고정(widths 없으면 균등).
// 대시보드 작업·워커 카드와 작업 상세가 같이 쓴다
const CELL = 'truncate border border-hairline-strong px-4 py-2 text-left align-middle'
export const TH = `${CELL} bg-surface-1 text-[14px] font-semibold text-dark`
export const TD = `${CELL} bg-surface-2 text-[16px] tabular-nums text-dark`

export function PlainTable({ cols, widths, minWidth = 640 }: { cols: [string, ReactNode][]; widths?: string[]; minWidth?: number }) {
  const w = widths ?? cols.map(() => `${100 / cols.length}%`)
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed border-collapse" style={{ minWidth }}>
        <colgroup>
          {w.map((x, i) => (
            <col key={i} style={{ width: x }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            {cols.map(([k]) => (
              <th key={k} scope="col" className={TH}>
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            {cols.map(([k, v]) => (
              <td key={k} className={TD}>
                {v}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  )
}
