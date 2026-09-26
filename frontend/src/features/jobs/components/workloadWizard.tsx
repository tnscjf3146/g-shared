'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Stepper } from '@/components/ui/stepper'
import { completeAdapterUpload, completeDatasetUpload, createDataset, createJob, getCatalog, requestAdapterUpload, requestDatasetUpload, uploadSignedFile } from '../api'
import { hashFile } from '../dataset-analysis'
import { clearDraft, loadDraft, saveDraft } from '../draft'
import { jobMessageFor } from '../errors'
import { suggestAdapterName } from '../format'
import { hasEnough, useWalletFunds } from '../hooks/use-wallet-funds'
import { adapterNameSchema } from '../schema'
import type { AutoMode, Catalog, DatasetMetadata, Estimate, JobSpec, TrainingConfig } from '../types'
import { StepConfig } from './stepConfig'
import { StepDataset } from './stepDataset'
import { StepGpu } from './stepGpu'
import { StepUpload } from './stepUpload'
import { StepWorkload } from './stepWorkload'

// /jobs/new "워크로드 등록" 5단계 위저드(job.md §4). 1 워크로드 → 2 설정 → 3 데이터셋(브라우저 분석) → 4 GPU 등급(메타데이터로 추천, 고르면 견적) → 5 업로드(지갑 잔액 ≥ 비용이면 업로드·등록, 부족하면 예치 안내)
// 데이터셋 원본과 입력 어댑터(.tar)는 Job 생성 뒤 서명 URL 로 올라간다(Dataset_Upload_Handoff · lora-adapter-frontend-작업).
// 어댑터 출발점은 둘 중 하나다: 내 완료된 학습 작업(parentJobId — 서버에 이미 있어 올릴 게 없다) 또는 .tar 파일 업로드(다른 데서 받아 온 어댑터)
// 모델은 G-shared 제공 모델만 사용한다(내 학습된 모델 가져오기는 제거). 백엔드 JobSpec.ownModel 계약은 유지하되 항상 false
/** 이어서 학습할(추론·평가라면 적용할) 어댑터 — 내 완료된 학습 작업 하나. 이름·버전은 목록이 준 표시용 값이고 정본은 서버 */
export type ParentAdapter = { jobId: number; name: string | null; version: number | null }
/** 어댑터 출발점: 없음(처음부터) / 내 완료 작업(v2·v3) / .tar 업로드. 고른 칸과 실제 값은 따로 둔다 — 골랐지만 아직 안 채운 상태를 구분해야 다음 단계를 막을 수 있다 */
export type AdapterSource = 'none' | 'job' | 'file'
/** 실제로 쓰이는 값만 — 전송·요약·hasAdapter 가 전부 이 둘을 본다 */
export const chosenParent = (s: WizardState): ParentAdapter | null => (s.adapterSource === 'job' ? s.parent : null)
export const chosenAdapterFile = (s: WizardState): File | null => (s.adapterSource === 'file' ? s.adapterFile : null)
/** 어댑터를 얹고 시작하는가 — 이어서 학습이거나 .tar 를 올렸거나 */
export const hasAdapter = (s: WizardState): boolean => chosenParent(s) !== null || chosenAdapterFile(s) !== null
/** 결과 어댑터 버전 — 이어서 학습만 부모+1(부모 버전을 모르면 null), 그 밖(처음부터·외부 파일)은 새 계보 v1 */
export const resultAdapterVersion = (s: WizardState): number | null => {
  const parent = chosenParent(s)
  return parent ? (parent.version ? parent.version + 1 : null) : 1
}
/** 이름을 비워 뒀을 때 등록되는 이름 — 이어서 학습이면 부모 이름을 잇고(서버 기본값과 같다), 아니면 모델 기반 제안 */
export const defaultAdapterName = (s: WizardState, modelName: string | undefined): string => chosenParent(s)?.name?.trim() || suggestAdapterName(modelName)
/** 실제로 등록될 결과 어댑터 이름 — 2단계 안내·5단계 요약·전송이 전부 이 값을 써야 화면과 결과가 어긋나지 않는다 */
export const resultAdapterName = (s: WizardState, modelName: string | undefined): string => s.adapterName.trim() || defaultAdapterName(s, modelName)
export type WizardState = {
  workloadId: number | null
  baseModelId: number | null
  adapterSource: AdapterSource // 어댑터 출발점으로 고른 칸
  parent: ParentAdapter | null // 'job': 이어서 학습(v2, v3…) 할 내 완료 작업
  adapterFile: File | null // 'file': 올릴 입력 어댑터 .tar
  adapterName: string // 결과 어댑터 이름(= 다운로드 파일 이름). 빈 문자열이면 제안값으로 등록한다
  level: AutoMode // 학습 수준(학습 워크로드만)
  custom: TrainingConfig | null | 'invalid' // 고급: 직접 설정(있으면 level 대신). 'invalid' = 고급을 열었지만 값이 틀림 → 다음 단계 차단
  datasetFile: File | null
  datasetMetadata: DatasetMetadata | null // 파일이면 브라우저 분석 결과, 기존이면 카탈로그 값
  analyzing: boolean
  gpuId: number | null
  estimate: Estimate | null
}
const INITIAL: WizardState = {
  workloadId: null, baseModelId: null, adapterSource: 'none', parent: null, adapterFile: null, adapterName: '', level: 'BALANCED', custom: null,
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
    hasAdapter: hasAdapter(s), // 어댑터를 얹고 시작하면 VRAM·시간 추정이 달라진다
    ownModel: false, // 내 모델 가져오기 제거 — 항상 제공 모델
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
  const [restored, setRestored] = useState<'loading' | 'fresh' | 'draft'>('loading') // 초안(draft.ts) 복원 — 끝나기 전엔 저장도 렌더도 안 한다
  const { funds, recheck } = useWalletFunds(step === 4) // 5단계에 들어오면 지갑·잔액 확인 → 하단 [업로드] 활성 판단
  const patch = (p: Partial<WizardState>) => {
    setState((s) => ({ ...s, ...p }))
    setError(null)
  }

  useEffect(() => {
    getCatalog()
      .then(setCatalog)
      .catch(() => setCatalog('error'))
    loadDraft().then((d) => {
      if (d?.state.workloadId) {
        // 아무것도 안 고른 초안은 없는 것으로.
        // INITIAL 위에 얹는다 — 초안은 예전 버전의 화면이 저장한 것일 수 있고, 그때 없던 필드를 통째로 덮어쓰면
        // undefined 가 들어와 렌더에서 터진다(2026-09-17: adapterName 추가 뒤 옛 초안이 남은 브라우저에서 2단계 크래시)
        setState({ ...INITIAL, ...d.state })
        setStep(d.step)
        setMaxReached(d.maxReached)
        setRestored('draft')
      } else setRestored('fresh')
    })
  }, [])
  // 바뀔 때마다 초안 저장. 분석 도중 새로고침이면 분석은 이어 갈 수 없으니 파일을 다시 고르게 한다
  useEffect(() => {
    if (restored === 'loading') return
    saveDraft({ state: state.analyzing ? { ...state, analyzing: false, datasetFile: null } : state, step, maxReached })
  }, [restored, state, step, maxReached])
  const reset = () => {
    setState(INITIAL)
    setStep(0)
    setMaxReached(0)
    setError(null)
    setRestored('fresh')
  }

  if (catalog === null || restored === 'loading') return <div aria-busy className="card h-96 w-full animate-pulse" />
  if (catalog === 'error') {
    return (
      <section className="card flex w-full flex-col items-start gap-3 p-10">
        <p className="text-label-sm text-dark">선택 항목을 불러오지 못했습니다.</p>
        <button type="button" onClick={() => location.reload()} className="text-caption text-primary hover:underline">
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
      if (state.custom === 'invalid') return '고급 학습 설정 값을 확인해 주세요.'
      if (state.adapterSource === 'job' && !state.parent) return '이어서 학습할 어댑터를 선택해 주세요. 처음부터 학습하려면 [처음부터 학습]을 고르세요.'
      if (state.adapterSource === 'file' && !state.adapterFile) return '어댑터 .tar 파일을 올려 주세요. 처음부터 학습하려면 [처음부터 학습]을 고르세요.'
      // 어댑터 이름은 학습(결과물이 어댑터)일 때만 쓴다 — 유형을 바꾼 뒤 남은 값으로 막지 않는다
      const name = workload?.kind === 'TRAIN_LORA' ? state.adapterName.trim() : ''
      const parsed = name ? adapterNameSchema.safeParse(name) : null
      if (parsed && !parsed.success) return parsed.error.issues[0]?.message ?? '어댑터 이름을 확인해 주세요.'
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
        // 어댑터 이름은 학습일 때만. 비워 두면 2단계가 보여 준 기본값(부모 이름 또는 제안값)을 그대로 보낸다 — 화면과 결과가 어긋나지 않게
        adapterName: workload?.kind === 'TRAIN_LORA' ? resultAdapterName(state, catalog.baseModels.find((m) => m.id === spec.baseModelId)?.name) : undefined,
        parentJobId: chosenParent(state)?.jobId ?? null,
        hasAdapter: spec.hasAdapter,
        // 4단계에서 고른 GPU 등급. 서버는 이 등급의 워커 중 순위가 가장 높은 한 대를 배정한다 —
        // 보내지 않으면 서버가 1순위를 고르므로, 화면이 "고를 수 있다"고 말한 것과 결과가 어긋난다.
        gpuId: state.gpuId,
      })
      // 원본 파일 업로드 — objectKey·storageUri 는 백엔드 몫, 프론트는 발급받은 서명 URL 에 PUT 만
      setSubmitting('데이터셋 업로드 중…')
      const uploadUrl = await requestDatasetUpload(jobId)
      await uploadSignedFile(uploadUrl, state.datasetFile)
      setSubmitting('업로드 확인 중…')
      const verified = await completeDatasetUpload(jobId)
      if (!verified) {
        setError(`데이터셋 업로드 검증에 실패했습니다. 작업 #${jobId} 은(는) 예치 대기로 남아 있으니 상세에서 취소한 뒤, 분석한 파일을 그대로 다시 등록해 주세요.`)
        setSubmitting(null)
        return
      }
      // 입력 어댑터 .tar — 같은 흐름(서명 URL → PUT → 검증). 해시는 큰 파일이면 오래 걸려 문구로 알린다
      const adapterFile = chosenAdapterFile(state)
      if (adapterFile) {
        setSubmitting('어댑터 해시 계산 중…')
        const sha256 = (await hashFile(adapterFile)).replace(/^0x/, '') // 어댑터는 0x 없는 소문자 64자(데이터셋 dataHash 와 표기가 다르다)
        setSubmitting('어댑터 업로드 중…')
        await uploadSignedFile(await requestAdapterUpload(jobId), adapterFile)
        setSubmitting('업로드 확인 중…')
        if (!(await completeAdapterUpload(jobId, { sha256, sizeBytes: adapterFile.size }))) {
          setError(`어댑터 업로드 검증에 실패했습니다. 작업 #${jobId} 은(는) 예치 대기로 남아 있으니 상세에서 취소한 뒤 다시 등록해 주세요.`)
          setSubmitting(null)
          return
        }
      }
      await clearDraft()
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
      {restored === 'draft' && (
        <p role="status" className="flex items-center justify-between gap-3 rounded-md bg-neutral-tint px-4 py-3 text-caption text-dark">
          이전에 작성하던 내용을 이어서 보여 드립니다.
          <button type="button" onClick={reset} disabled={submitting !== null} className="shrink-0 text-primary hover:underline">
            처음부터
          </button>
        </p>
      )}

      <section className="card animate-enter flex flex-col gap-6 p-8" key={step}>
        {step === 0 && <StepWorkload catalog={catalog} state={state} onChange={patch} />}
        {step === 1 && <StepConfig catalog={catalog} state={state} onChange={patch} />}
        {step === 2 && <StepDataset catalog={catalog} state={state} onChange={patch} />}
        {step === 3 && spec && <StepGpu spec={spec} state={state} onChange={patch} />}
        {step === 4 && spec && <StepUpload catalog={catalog} spec={spec} state={state} funds={funds} onRecheck={recheck} />}

        {(error || submitting) && (
          <p role={error ? 'alert' : 'status'} className={`rounded-md px-4 py-3 text-caption ${error ? 'bg-danger-tint text-danger' : 'bg-neutral-tint text-dark'}`}>
            {error ?? submitting}
          </p>
        )}

        <div className="flex items-center justify-between border-t border-hairline pt-5">
          {/* 1단계에선 돌아갈 곳이 없다 — 회색 버튼 대신 숨긴다. invisible 로 자리는 지켜 "n/5"와 [다음] 위치가 안 흔들리게 */}
          <Button variant="secondary" className={step === 0 ? 'invisible' : undefined} disabled={step === 0 || submitting !== null} onClick={() => goTo(step - 1)}>
            이전
          </Button>
          <span className="text-caption tabular-nums text-text-muted">
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
