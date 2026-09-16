import { Fragment, type ReactNode } from 'react'

// 장식 없는 격자 표 한 줄: 헤더 행(라벨) + 값 행. 모든 칸 테두리, 회색 헤더, 각진 모서리, 컬럼 폭 고정(widths 없으면 균등).
// 대시보드 작업·워커 카드와 작업 상세가 같이 쓴다.
// 좁은 화면(md 미만)에선 가로 스크롤로 칸이 잘리지 않게 "라벨 : 값" 세로 목록으로 바꾼다
const CELL = 'truncate border border-hairline-strong px-4 py-2 text-left align-middle'
export const TH = `${CELL} bg-surface-1 text-[14px] font-semibold text-dark`
export const TD = `${CELL} bg-surface-2 text-[16px] tabular-nums text-dark`

export function PlainTable({ cols, widths, minWidth = 640 }: { cols: [string, ReactNode][]; widths?: string[]; minWidth?: number }) {
  const w = widths ?? cols.map(() => `${100 / cols.length}%`)
  return (
    <>
      {/* 좁은 화면: 세로로 쌓기 — 아무것도 안 잘림 */}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-[15px] md:hidden">
        {cols.map(([k, v]) => (
          <Fragment key={k}>
            <dt className="text-text-muted">{k}</dt>
            <dd className="min-w-0 truncate text-right tabular-nums text-dark">{v}</dd>
          </Fragment>
        ))}
      </dl>
      {/* 넓은 화면: 기존 격자 표 */}
      <div className="hidden overflow-x-auto md:block">
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
    </>
  )
}
