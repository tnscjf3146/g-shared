'use client'

import { useState } from 'react'
import { useZodForm } from '@/features/auth/hooks/use-zod-form'
import { Field } from '@/components/ui/field'
import { FileDrop } from '@/components/ui/fileDrop'
import { Input } from '@/components/ui/input'
import { AUTO_MODE_META } from '../format'
import { customConfigFormSchema } from '../schema'
import type { AutoMode, Catalog } from '../types'
import type { WizardState } from './workloadWizard'

// 2단계: 모델·설정. 제공 모델만 사용(내 모델 가져오기 제거) · 학습 수준(학습 워크로드만) · 기존 어댑터 첨부 · 고급(직접 설정)
const ADAPTER_ACCEPT = '.safetensors,.bin,.pt,.zip'

type Props = { catalog: Catalog; state: WizardState; onChange: (p: Partial<WizardState>) => void }

export function StepConfig({ catalog, state, onChange }: Props) {
  const workload = catalog.workloads.find((w) => w.id === state.workloadId)
  const model = catalog.baseModels.find((m) => m.id === state.baseModelId)
  const isTrain = workload?.kind === 'TRAIN_LORA'
  const [advanced, setAdvanced] = useState(state.custom !== null)
  const c = typeof state.custom === 'object' ? state.custom : null
  const custom = useZodForm(
    customConfigFormSchema,
    c ? { epochs: String(c.epochs), batchSize: String(c.batchSize), learningRate: String(c.learningRate), maxLength: String(c.maxLength) } : { epochs: '3', batchSize: '16', learningRate: '0.00003', maxLength: '512' },
  )
  const str = (v: unknown) => (v == null ? '' : String(v))

  // 고급 값이 바뀔 때마다 즉시 검증(필드 오류 표시). 유효하면 상태에 반영, 틀리면 'invalid'(위저드가 다음 단계 차단)
  const setCustom = (k: 'epochs' | 'batchSize' | 'learningRate' | 'maxLength', v: string) => {
    custom.set(k, v)
    const data = custom.validate({ ...custom.values, [k]: v })
    onChange({ custom: data ?? 'invalid' })
  }
  const toggleAdvanced = (on: boolean) => {
    setAdvanced(on)
    onChange({ custom: on ? (custom.validate() ?? 'invalid') : null })
  }

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">2. 모델 · 설정</h2>
        <p className="text-[15px] text-text-muted">
          {workload?.name} · {model?.name ?? '모델 미선택'}
        </p>
      </header>

      {/* 모델 — G-shared 제공 모델만(1단계에서 선택). 읽기 전용 확인 */}
      <fieldset className="flex flex-col gap-2">
        <legend className="text-[13px] font-medium text-text-muted">모델</legend>
        <div className="card-quiet flex items-center justify-between gap-3 p-4">
          <span className="text-[15px] font-semibold text-dark">{model ? model.name : '1단계에서 모델을 선택해 주세요'}</span>
          {model && <span className="text-[13px] text-text-muted">{model.params}</span>}
        </div>
      </fieldset>

      {/* 학습 수준 — 학습 워크로드만 */}
      {isTrain && (
        <fieldset className="flex flex-col gap-2">
          <legend className="text-[13px] font-medium text-text-muted">학습 수준</legend>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {(Object.keys(AUTO_MODE_META) as AutoMode[]).map((k) => {
              const on = state.level === k && !advanced
              return (
                <label key={k} className={`card-quiet flex cursor-pointer flex-col gap-1 p-4 transition-colors ${advanced ? 'opacity-50' : ''} ${on ? 'border-primary/60 ring-2 ring-primary/30' : 'hover:border-border'}`}>
                  <input type="radio" name="level" value={k} checked={state.level === k} disabled={advanced} onChange={() => onChange({ level: k })} className="sr-only" />
                  <span className="text-[15px] font-semibold text-dark">{AUTO_MODE_META[k].label}</span>
                  <span className="text-[13px] text-text-muted">{AUTO_MODE_META[k].description}</span>
                </label>
              )
            })}
          </div>
          {/* 네이티브 <details> — 고급 설정은 접어 둔다 */}
          <details open={advanced} onToggle={(e) => toggleAdvanced((e.target as HTMLDetailsElement).open)} className="card-quiet mt-1">
            <summary className="cursor-pointer text-[14px] font-semibold text-dark marker:text-text-muted">고급: 학습 설정 직접 입력</summary>
            <div className="grid grid-cols-2 gap-4 pt-4 md:grid-cols-4">
              {(
                [
                  ['epochs', '에폭', '1'],
                  ['batchSize', '배치 크기', '1'],
                  ['learningRate', '학습률', 'any'],
                  ['maxLength', '최대 길이', '1'],
                ] as const
              ).map(([k, label, step]) => (
                <Field key={k} id={k} label={label} error={custom.errors[k]}>
                  <Input id={k} type="number" inputMode="decimal" step={step} min={0} value={str(custom.values[k])} onChange={(e) => setCustom(k, e.target.value)} aria-invalid={!!custom.errors[k]} aria-describedby={custom.describedBy(k)} />
                </Field>
              ))}
            </div>
            <p className="pt-3 text-[12px] text-text-muted">직접 입력하면 학습 수준 대신 이 값으로 견적을 냅니다. 상한은 없고, 실행 가능 여부(VRAM 등)는 서버가 판단합니다.</p>
          </details>
        </fieldset>
      )}

      {/* 어댑터 — 학습: 이어서 학습 / 추론·평가: 적용 */}
      <fieldset className="flex flex-col gap-2">
        <legend className="text-[13px] font-medium text-text-muted">{isTrain ? '기존 어댑터 (선택) — 이어서 학습' : '어댑터 (선택) — 모델에 적용'}</legend>
        <FileDrop file={state.adapterFile} onFile={(f) => onChange({ adapterFile: f })} accept={ADAPTER_ACCEPT} label="LoRA 어댑터 파일을 끌어놓거나 클릭해 선택" hint={isTrain ? '없으면 처음부터 학습합니다' : '없으면 모델만으로 진행합니다'} />
      </fieldset>
    </>
  )
}
