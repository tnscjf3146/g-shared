'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Stepper } from '@/components/ui/stepper'
import { completeDatasetUpload, createDataset, createJob, getCatalog, requestDatasetUpload, uploadDatasetFile } from '../api'
import { hashFile } from '../dataset-analysis'
import { jobMessageFor } from '../errors'
import { hasEnough, useWalletFunds } from '../hooks/use-wallet-funds'
import type { AutoMode, Catalog, DatasetMetadata, Estimate, JobSpec, TrainingConfig } from '../types'
import { StepConfig } from './stepConfig'
import { StepDataset } from './stepDataset'
import { StepGpu } from './stepGpu'
import { StepUpload } from './stepUpload'
import { StepWorkload } from './stepWorkload'

// /jobs/new "워크로드 등록" 5단계 위저드(job.md §4). 1 워크로드 → 2 설정 → 3 데이터셋(브라우저 분석) → 4 GPU 등급(메타데이터로 추천, 고르면 견적) → 5 업로드(지갑 잔액 ≥ 비용이면 업로드·등록, 부족하면 예치 안내)
// 파일(데이터셋·어댑터·내 모델)은 브라우저에만 있다. 서버엔 3단계에서 뽑은 메타데이터(+파일 해시)만 보낸다 — 파일 저장 위치 확정 전(백엔드 안내 2026-09-10 §4)
export type WizardState = {
  workloadId: number | null
  baseModelId: number | null
  modelSource: 'PROVIDED' | 'OWN' // 제공 모델 / 내가 가진 학습된 모델
  ownModelFile: File | null
  adapterFile: File | null // 기존 LoRA 어댑터(학습: 이어서 / 추론·평가: 적용)
  level: AutoMode // 학습 수준(학습 워크로드만)
  custom: TrainingConfig | null | 'invalid' // 고급: 직접 설정(있으면 level 대신). 'invalid' = 고급을 열었지만 값이 틀림 → 다음 단계 차단
  datasetFile: File | null
  datasetMetadata: DatasetMetadata | null // 파일이면 브라우저 분석 결과, 기존이면 카탈로그 값
  analyzing: boolean
  gpuId: number | null
  estimate: Estimate | null
}
const INITIAL: WizardState = {
  workloadId: null, baseModelId: null, modelSource: 'PROVIDED', ownModelFile: null, adapterFile: null, level: 'BALANCED', custom: null,
  datasetFile: null, datasetMetadata: null, analyzing: false, gpuId: null, estimate: null,
}
const STEPS = [{ label: '워크로드' }, { label: '설정' }, { label: '데이터셋' }, { label: 'GPU 등급 · 견적' }, { label: '업로드' }]

/** GPU 추천·견적이 공통으로 받는 스펙 — 파일 없이 메타데이터만 */
function toSpec(s: WizardState): JobSpec | null {
  if (!s.workloadId || !s.baseModelId || !s.datasetMetadata) return null
  const custom = typeof s.custom === 'object' ? s.custom : null
  return {
    workloadId: s.workloadId,
    baseModelId: s.baseModelId,
    datasetMetadata: s.datasetMetadata,
    trainingMode: custom ? 'CUSTOM' : 'AUTO',
    autoMode: custom ? undefined : s.level,
    trainingConfig: custom ?? undefined,
    hasAdapter: s.adapterFile !== null,
    ownModel: s.modelSource === 'OWN',
  }
}

export function WorkloadWizard() {
  const router = useRouter()
  const [catalog, setCatalog] = useState<Catalog | null | 'error'>(null)
  const [state, setState] = useState<WizardState>(INITIAL)
  const [step, setStep] = useState(0)
  const [maxReached, setMaxReached] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState<string | null>(null) // 진행 문구
  const { funds, recheck } = useWalletFunds(step === 4) // 5단계에 들어오면 지갑·잔액 확인 → 하단 [업로드] 활성 판단
  const patch = (p: Partial<WizardState>) => {
    setState((s) => ({ ...s, ...p }))
    setError(null)
  }

  useEffect(() => {
    getCatalog()
      .then(setCatalog)
      .catch(() => setCatalog('error'))
  }, [])

  if (catalog === null) return <div aria-busy className="card h-96 w-full animate-pulse" />
  if (catalog === 'error') {
    return (
      <section className="card flex w-full flex-col items-start gap-3 p-10">
        <p className="text-[15px] text-dark">선택 항목을 불러오지 못했습니다.</p>
        <button type="button" onClick={() => location.reload()} className="text-[14px] text-primary hover:underline">
          다시 시도 →
        </button>
      </section>
    )
  }

  const workload = catalog.workloads.find((w) => w.id === state.workloadId) ?? null
  const spec = toSpec(state)

  // 단계별 완료 조건(job.md §4)
  const validate = (i: number): string | null => {
    if (i === 0) {
      if (!state.workloadId) return '워크로드 유형을 선택해 주세요.'
      if (!state.baseModelId) return '모델을 선택해 주세요.'
    }
    if (i === 1) {
      if (state.modelSource === 'OWN' && !state.ownModelFile) return '가져올 모델 파일을 첨부하거나 제공 모델을 선택해 주세요.'
      if (state.custom === 'invalid') return '고급 학습 설정 값을 확인해 주세요.'
    }
    if (i === 2) {
      if (!state.datasetFile) return workload?.kind === 'INFERENCE' ? '입력 데이터 파일을 올려 주세요.' : '데이터셋 파일을 올려 주세요.'
      if (state.analyzing) return '데이터셋 분석이 끝날 때까지 잠시 기다려 주세요.'
      if (!state.datasetMetadata) return '데이터셋을 분석하지 못했습니다. 파일 형식을 확인해 주세요.'
    }
    if (i === 3) {
      if (!state.gpuId) return 'GPU 등급을 선택해 주세요.'
      if (!state.estimate) return '견적을 받는 중입니다. 잠시 기다려 주세요.'
    }
    return null
  }
  const next = () => {
    const msg = validate(step)
    if (msg) return setError(msg)
    const n = step + 1
    setStep(n)
    setMaxReached((m) => Math.max(m, n))
  }
  const goTo = (i: number) => {
    if (i <= maxReached && !submitting) {
      setStep(i)
      setError(null)
    }
  }

  // 마지막: 데이터셋 메타 등록 → POST /jobs → 원본 파일 업로드(서명 URL) → 검증 → 상세로. (Dataset_Upload_Handoff 2026-09-15)
  // 업로드·검증에 실패하면 상세로 보내지 않는다 — 예치까지 진행되면 Worker 가 파일을 못 받아 실행을 거부하기 때문
  const submit = async () => {
    if (!spec || !state.gpuId || !state.estimate || !state.datasetFile) return
    setError(null)
    try {
      setSubmitting('데이터셋 등록 중…')
      const meta = spec.datasetMetadata
      const { datasetId } = await createDataset({
        ...meta,
        effectiveTokens: meta.totalTokens, // 과금 기준. 패딩·잘림 반영값은 서버(AI)가 계산하는 게 맞아 지금은 totalTokens 로 — 백엔드도 견적에서 같은 우회를 쓴다
        dataHash: await hashFile(state.datasetFile),
        storageUri: null,
      })
      setSubmitting('워크로드 등록 중…')
      const { jobId } = await createJob({
        workloadId: spec.workloadId,
        baseModelId: spec.baseModelId,
        datasetId,
        trainingMode: spec.trainingMode,
        trainingPriority: spec.trainingMode === 'AUTO' ? state.level : undefined, // 견적의 autoMode 와 같은 값, 이름만 다름
        trainingConfig: spec.trainingMode === 'CUSTOM' ? state.estimate.trainingConfig : undefined,
      })
      // 원본 파일 업로드 — objectKey·storageUri 는 백엔드 몫, 프론트는 발급받은 서명 URL 에 PUT 만
      setSubmitting('데이터셋 업로드 중…')
      const uploadUrl = await requestDatasetUpload(jobId)
      await uploadDatasetFile(uploadUrl, state.datasetFile)
      setSubmitting('업로드 확인 중…')
      const verified = await completeDatasetUpload(jobId)
      if (!verified) {
        setError(`데이터셋 업로드 검증에 실패했습니다. 작업 #${jobId} 은(는) 예치 대기로 남아 있으니 상세에서 취소한 뒤, 분석한 파일을 그대로 다시 등록해 주세요.`)
        setSubmitting(null)
        return
      }
      router.replace(`/jobs/${jobId}`)
    } catch (e) {
      setError(jobMessageFor(e))
      setSubmitting(null)
    }
  }

  const last = step === STEPS.length - 1
  return (
    <div className="flex w-full flex-col gap-6">
      <Stepper steps={STEPS} current={step} maxReached={maxReached} onSelect={goTo} />

      <section className="card animate-enter flex flex-col gap-6 p-8" key={step}>
        {step === 0 && <StepWorkload catalog={catalog} state={state} onChange={patch} />}
        {step === 1 && <StepConfig catalog={catalog} state={state} onChange={patch} />}
        {step === 2 && <StepDataset catalog={catalog} state={state} onChange={patch} />}
        {step === 3 && spec && <StepGpu spec={spec} state={state} onChange={patch} />}
        {step === 4 && spec && <StepUpload catalog={catalog} spec={spec} state={state} funds={funds} onRecheck={recheck} />}

        {(error || submitting) && (
          <p role={error ? 'alert' : 'status'} className={`rounded-md px-4 py-3 text-[14px] ${error ? 'bg-danger-tint text-danger' : 'bg-neutral-tint text-dark'}`}>
            {error ?? submitting}
          </p>
        )}

        <div className="flex items-center justify-between border-t border-hairline pt-5">
          <Button variant="secondary" disabled={step === 0 || submitting !== null} onClick={() => goTo(step - 1)}>
            이전
          </Button>
          <span className="text-[13px] tabular-nums text-text-muted">
            {step + 1} / {STEPS.length}
          </span>
          {last ? (
            // 지갑 미연결·잔액 부족이면 회색(비활성), 잔액이 충분하면 초록(활성)
            (() => {
              const enough = hasEnough(funds, state.estimate?.estimatedCost ?? 0)
              return (
                <Button size="lg" variant={enough ? 'primary' : 'secondary'} disabled={!enough || submitting !== null} onClick={submit} title={enough ? undefined : funds.status === 'nowallet' ? '지갑을 먼저 연결하세요' : '지갑 잔액이 예상 비용보다 적습니다'}>
                  {submitting ? '업로드·등록 중…' : '업로드'}
                </Button>
              )
            })()
          ) : (
            <Button size="lg" onClick={next} disabled={state.analyzing && step === 2}>
              {step === 2 && state.analyzing ? '분석 중…' : `다음: ${STEPS[step + 1].label}`}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}
