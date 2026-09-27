'use client'

import { useEffect, useState } from 'react'
import { StatusBadge } from '@/components/ui/statusBadge'
import { estimateJob, getGpuOptions } from '../api'
import { jobMessageFor } from '../errors'
import type { GpuFit, GpuOption, JobSpec } from '../types'
import { EstimateCard } from './estimateCard'
import type { WizardState } from './workloadWizard'

// 4단계: GPU 등급 — 백엔드가 메타데이터·모델·설정으로 매긴 적합도 목록에서 고르면 **바로 아래에 견적**이 뜬다(job.md §4). 파일은 아직 서버에 없다
const FIT: Record<GpuFit, { label: string; tone: 'success' | 'primary' | 'warning' | 'danger' }> = {
  RECOMMENDED: { label: '추천', tone: 'success' },
  OK: { label: '가능', tone: 'primary' },
  TIGHT: { label: '빡빡함', tone: 'warning' },
  INSUFFICIENT: { label: 'VRAM 부족', tone: 'danger' },
}

/**
 * 카드·요약에 쓰는 등급 이름. 제조사 접두어와 꼬리의 "GPU" 를 뗀다.
 *
 * <p>"GPU 등급" 목록 안에서 이름마다 "GPU" 를 또 붙일 이유가 없고, 무엇보다 이 꼬리가 붙는 것은
 * 실제 워커가 보고한 모델명뿐이라(예: "RTX 4070 Laptop GPU") 발표용 목업 항목과 한눈에 갈렸다.
 * 줄 수까지 달라져(두 줄 vs 한 줄) 카드 높이가 들쭉날쭉했다.
 */
const gpuName = (model: string) => model.replace('NVIDIA GeForce ', '').replace(/\s*GPU$/i, '')

/** 5단계 요약에 그대로 쓰이는 등급 표시 이름 — 카드에 보이는 것과 같은 규칙 */
const gpuLabel = (g: GpuOption) => `${gpuName(g.model)} · ${Math.round(g.vramMb / 1024)} GB`

type Props = { spec: JobSpec; state: WizardState; onChange: (p: Partial<WizardState>) => void }
type Result = { key: string; options: GpuOption[] } | { key: string; error: string }

export function StepGpu({ spec, state, onChange }: Props) {
  const key = JSON.stringify(spec) // 스펙이 바뀌면 다시 추천. 결과에 key 를 같이 두어 "옛 스펙의 결과" 를 로딩으로 취급(effect 안 동기 setState 없음)
  const [result, setResult] = useState<Result | null>(null)
  // 다시 시도 — 스펙도 등급도 그대로일 때 목록·견적을 **다시 부르는** 유일한 길.
  // 이게 없으면 잠깐의 409 하나가 막다른 길이 된다: 화면은 "잠시 후 다시 시도해 주세요" 라고 하는데
  // 두 효과 모두 [key, gpuId] 가 바뀔 때만 다시 돌고, 이미 고른 등급을 다시 눌러도 onChange 가 안 난다.
  // 기다려도 아무 일이 일어나지 않아, 등급을 바꿨다 되돌아오는 우연한 조작으로만 풀렸다(리뷰 지적)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    getGpuOptions(JSON.parse(key) as JobSpec)
      .then((options) => setResult({ key, options }))
      .catch((e) => setResult({ key, error: jobMessageFor(e) }))
  }, [key, retry])

  const current = result && result.key === key ? result : null
  const options = current && 'options' in current ? current.options : null
  // VRAM 부족(INSUFFICIENT) 등급은 고를 수 없으니 목록에서 아예 숨긴다
  const visible = options ? options.filter((g) => g.fit !== 'INSUFFICIENT') : null

  // GPU 를 고르면 견적. 스펙·GPU 가 바뀌면 다시(부모가 gpuId 변경 시 estimate 를 null 로 비운다)
  // 에러에 "어느 스펙·어느 등급의 것인지" 를 같이 담는다. 위의 result 와 같은 방식이다 —
  // 지난 선택의 에러 지우기를 효과 안 동기 setState 대신 렌더에서 걸러 내는 것으로 해결한다
  const [lastError, setLastError] = useState<{ key: string; gpuId: number; message: string } | null>(null)
  const gpuId = state.gpuId
  useEffect(() => {
    if (gpuId == null) return
    // ignore: 등급을 빠르게 바꾸면 먼저 보낸 요청이 나중에 도착할 수 있다. 그대로 두면 옛 등급의 견적이
    // 새 선택 위를 덮어써서, 화면에 보이는 등급과 금액이 서로 다른 것이 된다(수정요청서 FE-06)
    let ignore = false
    estimateJob({ ...(JSON.parse(key) as JobSpec), gpuId })
      .then((e) => {
        if (ignore) return
        // 성공했으면 에러를 지운다. 렌더 필터만으로는 부족하다 — 같은 등급을 떠났다 되돌아오면
        // (A 실패 → B → 다시 A) 키가 도로 맞아떨어져, 새 견적이 떠 있는데 옛 에러 배너가 같이 남는다(리뷰 지적)
        setLastError(null)
        onChange({ estimate: e })
      })
      .catch((e) => {
        if (ignore) return
        setLastError({ key, gpuId, message: jobMessageFor(e) })
        // 실패했으면 **지난 견적도 치운다.** 안 치우면 "지금 작업 가능한 GPU가 없습니다" 바로 아래에
        // 예전 금액이 그대로 떠 있고, 위저드는 estimate 가 있다는 이유로 다음 단계까지 열어 준다 —
        // 사용자는 이미 틀린 가격을 보고 주문하게 된다. 실패한 순간에 확실한 것은 "지금은 모른다" 뿐이다
        onChange({ estimate: null })
      })
    return () => {
      ignore = true
    }
    // onChange 는 안정된 setState 래퍼
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, gpuId, retry])
  // 지금 고른 스펙·등급의 에러만 보여 준다 — 등급을 바꾸면 옛 에러는 저절로 사라진다
  const estimateError = lastError && lastError.key === key && lastError.gpuId === gpuId ? lastError.message : null

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">GPU 등급</h2>
        <p className="text-label-sm text-text-muted">데이터셋 분석 결과와 모델·설정을 기준으로 적합도를 매겼습니다. 등급을 고르면 아래에 견적이 나옵니다.</p>
      </header>

      {!current && (
        <div aria-busy aria-label="GPU 추천 계산 중" className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card-quiet h-28 animate-pulse" />
          ))}
        </div>
      )}
      {current && 'error' in current && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-caption text-danger">
          {current.error}
        </p>
      )}
      {visible && visible.length === 0 && (
        <p role="status" className="rounded-md bg-neutral-tint px-4 py-3 text-caption text-dark">
          현재 작업 가능한 GPU가 없습니다. 등급 목록은 켜져 있는 공급자 PC 로 만들어져서, 잠시 후 다시 오면 나타날 수 있습니다.
        </p>
      )}
      {visible && visible.length > 0 && (
        <fieldset className="flex flex-col gap-2">
          <legend className="sr-only">GPU 등급</legend>
          {/* AGENTS.md 카드 그리드 규칙: 1 → md 2 → lg 3. md 에서 바로 3열로 가면 카드가 규칙보다 한 칸 좁아져 이름이 두 줄로 접힌다 */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {visible.map((g) => {
              const on = state.gpuId === g.id
              return (
                <label
                  key={g.id}
                  className={`card-quiet flex cursor-pointer flex-col gap-2 p-4 transition-colors ${on ? 'border-primary/60 ring-2 ring-primary/30' : 'hover:border-border'}`}
                >
                  {/* 고른 등급의 표시 이름을 같이 적어 둔다 — 5단계 요약이 따로 /gpus 를 받지 않아도 되고, 목록이 아직 안 온 시점에도 "—" 가 되지 않는다 */}
                  <input
                    type="radio"
                    name="gpuId"
                    value={g.id}
                    checked={on}
                    // 고를 때 에러도 같이 지운다(이벤트 핸들러라 효과 안 동기 setState 문제가 없다) — 안 지우면
                    // A 실패 → B → 다시 A 로 돌아왔을 때 옛 에러가 새 요청이 도는 동안 그대로 떠 있다
                    onChange={() => {
                      setLastError(null)
                      onChange({ gpuId: g.id, gpuLabel: gpuLabel(g), estimate: null })
                    }}
                    className="sr-only"
                  />
                  <div className="flex items-start justify-between gap-2">
                    {/* text-balance: 그래도 접히는 긴 이름이 있으면 두 줄로 고르게 나눈다(마지막 한 단어만 떨어지는 고아 줄 방지) */}
                    <span className="text-balance text-label-sm font-semibold text-dark">{gpuName(g.model)}</span>
                    {/* shrink-0: 이름이 길면("RTX 4070 Laptop GPU") 배지가 눌려 "추/천" 으로 쪼개진다 */}
                    <StatusBadge tone={FIT[g.fit].tone} className="shrink-0 whitespace-nowrap">
                      {FIT[g.fit].label}
                    </StatusBadge>
                  </div>
                  <span className="text-caption tabular-nums text-text-muted">
                    {Math.round(g.vramMb / 1024)} GB · 대기 워커 {g.availableWorkers}대
                  </span>
                  {/* 서버가 주는 reason("예상 실행 시간이 가장 짧습니다" · "여유 4 GB 로 넉넉합니다")은 그리지 않는다.
                      이 줄이 붙는 것은 실제 워커에서 온 등급뿐이라(목업은 빈 문자열) 카드 높이와 정보량이 갈렸다.
                      고르는 데 필요한 값(VRAM·대기 워커 수·적합도 배지)은 위 두 줄에 이미 다 있다. */}
                </label>
              )
            })}
          </div>
          {visible[0] && <p className="text-hint tabular-nums text-text-muted">필요 VRAM 약 {Math.round(visible[0].requiredVramMb / 1024)} GB 기준. 대기 워커가 0대인 등급은 배정까지 기다릴 수 있습니다.</p>}
        </fieldset>
      )}

      {/* 견적 — GPU 선택 직후 */}
      {gpuId != null && estimateError && (
        // 목록도 같이 다시 받는다 — 견적이 409 라는 것은 그 등급의 워커가 빠졌다는 뜻이라,
        // 옆 카드의 "대기 워커 n대" 도 이미 옛 숫자다. 둘이 어긋난 채로 두면 화면이 서로 다른 말을 한다
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-danger-tint px-4 py-3">
          <p className="text-caption text-danger">{estimateError}</p>
          <button type="button" onClick={() => setRetry((n) => n + 1)} className="text-caption font-medium text-danger underline underline-offset-2">
            다시 시도
          </button>
        </div>
      )}
      {gpuId != null && !estimateError && !state.estimate && <div aria-busy aria-label="견적 계산 중" className="card card-featured h-36 animate-pulse" />}
      {gpuId != null && state.estimate && <EstimateCard estimate={state.estimate} stale={false} />}
    </>
  )
}
