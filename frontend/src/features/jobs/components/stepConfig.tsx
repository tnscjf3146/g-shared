'use client'

import { useState } from 'react'
import { Field } from '@/components/ui/field'
import { FileDrop } from '@/components/ui/fileDrop'
import { Input } from '@/components/ui/input'
import { useZodForm } from '@/features/auth/hooks/use-zod-form'
import { ADAPTER_ACCEPT, checkAdapterTar } from '../adapter-tar'
import { AUTO_MODE_META, adapterLabel } from '../format'
import { ADAPTER_NAME_MAX, adapterNameSchema, customConfigFormSchema } from '../schema'
import type { AutoMode, Catalog } from '../types'
import { AdapterPicker } from './adapterPicker'
import { chosenParent, defaultAdapterName, resultAdapterVersion, type AdapterSource, type WizardState } from './workloadWizard'

// 2단계: 모델·설정. 제공 모델만 사용(내 모델 가져오기 제거) · 학습 수준(학습 워크로드만) · 어댑터 출발점(처음부터 / 내 완료 작업 이어서 = v2·v3 / .tar 업로드) · 결과 어댑터 이름 · 고급(직접 설정)

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

  const [fileError, setFileError] = useState<string | null>(null) // 어댑터 .tar 즉시 검사 결과

  // 이번 학습 결과의 버전·기본 이름 — 5단계 요약·전송과 같은 계산(workloadWizard 헬퍼). 부모 버전을 모르면(백엔드 반영 전) 숫자를 말하지 않는다
  const chosen = chosenParent(state)
  const nextVersion = resultAdapterVersion(state)
  const suggested = defaultAdapterName(state, model?.name)
  const trimmed = state.adapterName.trim()
  const nameParsed = trimmed ? adapterNameSchema.safeParse(trimmed) : null
  const nameError = nameParsed && !nameParsed.success ? (nameParsed.error.issues[0]?.message ?? '어댑터 이름을 확인해 주세요.') : undefined

  // 고급 값이 바뀔 때마다 즉시 검증(필드 오류 표시). 유효하면 상태에 반영, 틀리면 'invalid'(위저드가 다음 단계 차단)
  const setCustom = (k: 'epochs' | 'batchSize' | 'learningRate' | 'maxLength', v: string) => {
    custom.set(k, v)
    const data = custom.validate({ ...custom.values, [k]: v })
    onChange({ custom: data ?? 'invalid' })
  }
  // 어댑터 .tar 는 고르는 즉시 검사한다 — 업로드까지 가서 422 로 되돌아오면 5단계에서야 알게 된다
  const pickAdapterFile = async (f: File | null) => {
    setFileError(null)
    if (!f) return onChange({ adapterFile: null })
    const problem = await checkAdapterTar(f)
    setFileError(problem)
    onChange({ adapterFile: problem ? null : f })
  }
  const toggleAdvanced = (on: boolean) => {
    setAdvanced(on)
    onChange({ custom: on ? (custom.validate() ?? 'invalid') : null })
  }

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">모델 · 설정</h2>
        <p className="text-label-sm text-text-muted">
          {workload?.name} · {model?.name ?? '모델 미선택'}
        </p>
      </header>

      {/* 모델 — G-shared 제공 모델만(1단계에서 선택). 읽기 전용 확인 */}
      <fieldset className="flex flex-col gap-2">
        <legend className="text-caption font-medium text-text-muted">모델</legend>
        <div className="card-quiet flex items-center justify-between gap-3 p-4">
          <span className="text-label-sm font-semibold text-dark">{model ? model.name : '1단계에서 모델을 선택해 주세요'}</span>
          {model && <span className="text-caption text-text-muted">{model.params}</span>}
        </div>
      </fieldset>

      {/* 학습 수준 — 학습 워크로드만 */}
      {isTrain && (
        <fieldset className="flex flex-col gap-2">
          <legend className="text-caption font-medium text-text-muted">학습 수준</legend>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {(Object.keys(AUTO_MODE_META) as AutoMode[]).map((k) => {
              const on = state.level === k && !advanced
              return (
                <label key={k} className={`card-quiet flex cursor-pointer flex-col gap-1 p-4 transition-colors ${advanced ? 'opacity-50' : ''} ${on ? 'border-primary/60 ring-2 ring-primary/30' : 'hover:border-border'}`}>
                  <input type="radio" name="level" value={k} checked={state.level === k} disabled={advanced} onChange={() => onChange({ level: k })} className="sr-only" />
                  <span className="text-label-sm font-semibold text-dark">{AUTO_MODE_META[k].label}</span>
                  <span className="text-caption text-text-muted">{AUTO_MODE_META[k].description}</span>
                </label>
              )
            })}
          </div>
          {/* 네이티브 <details> — 고급 설정은 접어 둔다 */}
          <details open={advanced} onToggle={(e) => toggleAdvanced((e.target as HTMLDetailsElement).open)} className="card-quiet mt-1">
            <summary className="cursor-pointer text-caption font-semibold text-dark marker:text-text-muted">고급: 학습 설정 직접 입력</summary>
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
            <p className="pt-3 text-hint text-text-muted">직접 입력한 값으로 견적을 냅니다 · 실행 가능 여부는 서버가 판단</p>
          </details>
        </fieldset>
      )}

      {/* 어댑터 출발점 — 처음부터 / 내 완료 작업 이어서(v2·v3) / .tar 업로드. 추론·평가는 "적용"의 의미 */}
      <fieldset className="flex flex-col gap-2">
        <legend className="text-caption font-medium text-text-muted">{isTrain ? '학습 시작점' : '어댑터 (선택) — 모델에 적용'}</legend>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {(
            [
              ['none', isTrain ? '처음부터 학습' : '어댑터 없이', isTrain ? '베이스 모델에서 시작해 v1 어댑터를 만듭니다.' : '베이스 모델만으로 진행합니다.'],
              ['job', isTrain ? '이어서 학습 (버전 올리기)' : '내 학습 결과 적용', isTrain ? '완료된 내 어댑터에 이어서 학습합니다 (v2, v3…).' : '완료된 내 학습 작업의 어댑터를 모델에 얹습니다.'],
              ['file', '어댑터 파일 올리기', '다른 데서 받아 온 어댑터 .tar 를 올려 사용합니다.'],
            ] as [AdapterSource, string, string][]
          ).map(([value, label, description]) => (
            <label key={value} className={`card-quiet flex cursor-pointer flex-col gap-1 p-4 transition-colors ${state.adapterSource === value ? 'border-primary/60 ring-2 ring-primary/30' : 'hover:border-border'}`}>
              <input
                type="radio"
                name="adapter-source"
                value={value}
                checked={state.adapterSource === value}
                onChange={() => {
                  setFileError(null)
                  onChange({ adapterSource: value })
                }}
                className="sr-only"
              />
              <span className="text-label-sm font-semibold text-dark">{label}</span>
              {/* break-keep: 한국어가 단어(어절) 중간에서 끊기지 않고 띄어쓰기 단위로 줄바꿈 */}
              <span className="text-caption break-keep text-text-muted">{description}</span>
            </label>
          ))}
        </div>

        {state.adapterSource === 'job' && (
          <div className="flex flex-col gap-1.5 pt-1">
            <AdapterPicker catalog={catalog} value={chosen} onChange={(p) => onChange({ parent: p })} describedBy="adapter-parent-hint" />
            <p id="adapter-parent-hint" className="text-hint text-text-muted">
              {chosen ? `${adapterLabel(chosen.jobId, chosen.name, chosen.version)} 에서 이어서 학습합니다. 파일을 다시 올릴 필요가 없습니다.` : '이어서 학습할 어댑터를 선택해 주세요.'}
            </p>
          </div>
        )}

        {state.adapterSource === 'file' && (
          <div className="flex flex-col gap-1.5 pt-1">
            <FileDrop
              file={state.adapterFile}
              onFile={(f) => void pickAdapterFile(f)}
              accept={ADAPTER_ACCEPT}
              label="LoRA 어댑터 .tar 파일을 끌어놓거나 클릭해 선택"
              hint="같은 기본 모델로 학습한 결과여야 합니다 · 1 GiB 이하"
            />
            {fileError ? (
              <p role="alert" className="text-caption text-danger">
                {fileError}
              </p>
            ) : (
              <p className="text-hint text-text-muted">비압축 .tar · adapter_config.json + adapter_model.safetensors 포함</p>
            )}
          </div>
        )}
      </fieldset>

      {/* 결과 어댑터 이름 — 다운로드 파일 이름이 된다. 학습 워크로드만(추론·평가는 어댑터를 만들지 않는다) */}
      {isTrain && (
        <Field id="adapterName" label="어댑터 이름" error={nameError} hint={<span className="text-hint text-text-muted">다운로드 파일 이름이 됩니다</span>}>
          <Input
            id="adapterName"
            value={state.adapterName}
            onChange={(e) => onChange({ adapterName: e.target.value })}
            placeholder={suggested}
            maxLength={ADAPTER_NAME_MAX}
            aria-invalid={!!nameError}
            aria-describedby={nameError ? 'adapterName-error' : 'adapterName-hint'}
          />
          <p id="adapterName-hint" className="text-hint text-text-muted">
            비워 두면 <span className="font-medium text-dark">{suggested}</span> 로 저장됩니다. {nextVersion ? `이번 학습 결과는 v${nextVersion} 입니다.` : ''} 등록 뒤에도 작업 상세에서 바꿀 수 있습니다.
          </p>
        </Field>
      )}
    </>
  )
}
