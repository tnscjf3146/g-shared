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

type Props = { spec: JobSpec; state: WizardState; onChange: (p: Partial<WizardState>) => void }
type Result = { key: string; options: GpuOption[] } | { key: string; error: string }

export function StepGpu({ spec, state, onChange }: Props) {
  const key = JSON.stringify(spec) // 스펙이 바뀌면 다시 추천. 결과에 key 를 같이 두어 "옛 스펙의 결과" 를 로딩으로 취급(effect 안 동기 setState 없음)
  const [result, setResult] = useState<Result | null>(null)

  useEffect(() => {
    getGpuOptions(JSON.parse(key) as JobSpec)
      .then((options) => setResult({ key, options }))
      .catch((e) => setResult({ key, error: jobMessageFor(e) }))
  }, [key])

  const current = result && result.key === key ? result : null
  const options = current && 'options' in current ? current.options : null
  // VRAM 부족(INSUFFICIENT) 등급은 고를 수 없으니 목록에서 아예 숨긴다
  const visible = options ? options.filter((g) => g.fit !== 'INSUFFICIENT') : null

  // GPU 를 고르면 견적. 스펙·GPU 가 바뀌면 다시(부모가 gpuId 변경 시 estimate 를 null 로 비운다)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const gpuId = state.gpuId
  useEffect(() => {
    if (gpuId == null) return
    estimateJob({ ...(JSON.parse(key) as JobSpec), gpuId })
      .then((e) => {
        setEstimateError(null)
        onChange({ estimate: e })
      })
      .catch((e) => setEstimateError(jobMessageFor(e)))
    // onChange 는 안정된 setState 래퍼
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, gpuId])

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
                  <input type="radio" name="gpuId" value={g.id} checked={on} onChange={() => onChange({ gpuId: g.id, estimate: null })} className="sr-only" />
                  <div className="flex items-start justify-between gap-2">
                    {/* text-balance: 카드 폭이 좁아 "RTX 4050 Laptop GPU" 가 두 줄이 되는데, 그냥 두면 마지막 "GPU" 한 단어만 넘어가 고아 줄이 된다 */}
                    <span className="text-balance text-label-sm font-semibold text-dark">{g.model.replace('NVIDIA GeForce ', '')}</span>
                    {/* shrink-0: 이름이 길면("RTX 4070 Laptop GPU") 배지가 눌려 "추/천" 으로 쪼개진다 */}
                    <StatusBadge tone={FIT[g.fit].tone} className="shrink-0 whitespace-nowrap">
                      {FIT[g.fit].label}
                    </StatusBadge>
                  </div>
                  <span className="text-caption tabular-nums text-text-muted">
                    {Math.round(g.vramMb / 1024)} GB · 대기 워커 {g.availableWorkers}대
                  </span>
                  <span className="text-hint text-text-muted">{g.reason}</span>
                </label>
              )
            })}
          </div>
          {visible[0] && <p className="text-hint tabular-nums text-text-muted">필요 VRAM 약 {Math.round(visible[0].requiredVramMb / 1024)} GB 기준. 대기 워커가 0대인 등급은 배정까지 기다릴 수 있습니다.</p>}
        </fieldset>
      )}

      {/* 견적 — GPU 선택 직후 */}
      {gpuId != null && estimateError && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-caption text-danger">
          {estimateError}
        </p>
      )}
      {gpuId != null && !estimateError && !state.estimate && <div aria-busy aria-label="견적 계산 중" className="card card-featured h-36 animate-pulse" />}
      {gpuId != null && state.estimate && <EstimateCard estimate={state.estimate} stale={false} />}
    </>
  )
}
