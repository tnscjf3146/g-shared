'use client'

import type { Catalog, WorkloadKind } from '../types'
import type { WizardState } from './workloadWizard'

// 1단계: 워크로드 유형(학습 LoRA / 추론 / 평가) → 그 유형을 지원하는 제공 모델
const KIND_ICON: Record<WorkloadKind, string> = { TRAIN_LORA: 'M4 12l4-4 4 4 4-4', INFERENCE: 'M4 8h9m0 0-3-3m3 3-3 3', EVALUATION: 'M3 12l3-3 3 3 5-6' }

type Props = { catalog: Catalog; state: WizardState; onChange: (p: Partial<WizardState>) => void }

export function StepWorkload({ catalog, state, onChange }: Props) {
  const models = state.workloadId ? catalog.baseModels.filter((m) => m.workloadIds.includes(state.workloadId!)) : []
  const workload = catalog.workloads.find((w) => w.id === state.workloadId)

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">워크로드</h2>
        <p className="text-label-sm text-text-muted">무엇을 할지 고르면 그 작업을 지원하는 모델이 나타납니다.</p>
      </header>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-caption font-medium text-text-muted">워크로드 유형</legend>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {catalog.workloads.map((w) => {
            const on = state.workloadId === w.id
            return (
              <label key={w.id} className={`card-quiet flex cursor-pointer flex-col gap-2 p-4 transition-colors ${on ? 'border-primary/60 ring-2 ring-primary/30' : 'hover:border-border'}`}>
                <input type="radio" name="workloadId" value={w.id} checked={on} onChange={() => onChange({ workloadId: w.id, baseModelId: null })} className="sr-only" />
                <span className="flex items-start gap-2">
                  <svg aria-hidden width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={`mt-0.5 shrink-0 ${on ? 'text-primary' : 'text-text-muted'}`}>
                    <path d={KIND_ICON[w.kind]} />
                  </svg>
                  {/* 한글이 글자 중간에서 끊기지 않게 낱말 단위로 줄바꿈(break-keep), 카드 높이는 두 줄로 고정 */}
                  <span className="line-clamp-2 h-[2lh] break-keep text-label-sm font-semibold text-dark">{w.name}</span>
                </span>
              </label>
            )
          })}
        </div>
      </fieldset>

      {workload && (
        <fieldset className="flex flex-col gap-2">
          <legend className="text-caption font-medium text-text-muted">모델 (G-shared 제공)</legend>
          {models.length === 0 ? (
            <p className="rounded-md bg-neutral-tint px-4 py-3 text-caption text-dark">이 워크로드를 지원하는 모델이 아직 없습니다.</p>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {models.map((m) => {
                const on = state.baseModelId === m.id
                return (
                  <label key={m.id} className={`card-quiet flex cursor-pointer items-center justify-between gap-3 p-4 transition-colors ${on ? 'border-primary/60 ring-2 ring-primary/30' : 'hover:border-border'}`}>
                    <input type="radio" name="baseModelId" value={m.id} checked={on} onChange={() => onChange({ baseModelId: m.id })} className="sr-only" />
                    <span className="text-label-sm font-semibold text-dark">{m.name}</span>
                    {m.params && <span className="rounded-xs bg-neutral-tint px-1.5 py-0.5 text-hint tabular-nums text-text-muted">{m.params}</span>}
                  </label>
                )
              })}
            </div>
          )}
        </fieldset>
      )}
    </>
  )
}
