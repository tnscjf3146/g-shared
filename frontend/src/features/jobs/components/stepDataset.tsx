'use client'

import { useState } from 'react'
import { FileDrop } from '@/components/ui/fileDrop'
import { analyzeDataset } from '../dataset-analysis'
import { formatBytes } from '../format'
import type { Catalog, WorkloadKind } from '../types'
import type { WizardState } from './workloadWizard'

// 3단계: 데이터셋 — 파일 끌어놓기/클릭. 파일은 서버로 안 가고 **브라우저에서 분석**해 메타데이터 5값만 뽑는다(job.md §4-3)
const DATASET_ACCEPT = '.jsonl,.json,.csv,.txt'
const COPY: Record<WorkloadKind, { title: string; desc: string }> = {
  TRAIN_LORA: { title: '학습 데이터셋', desc: '한 줄에 샘플 하나(JSONL 권장). 지시-응답 쌍 또는 텍스트-라벨 쌍.' },
  INFERENCE: { title: '입력 데이터', desc: '모델에 넣을 입력 샘플 목록. 결과는 같은 순서로 돌아옵니다.' },
  EVALUATION: { title: '평가 데이터셋', desc: '정답이 포함된 샘플. 지표 계산에 씁니다.' },
}

type Props = { catalog: Catalog; state: WizardState; onChange: (p: Partial<WizardState>) => void }

export function StepDataset({ catalog, state, onChange }: Props) {
  const kind = catalog.workloads.find((w) => w.id === state.workloadId)?.kind ?? 'TRAIN_LORA'
  const copy = COPY[kind]
  const [progress, setProgress] = useState(0) // 읽은 바이트
  const [failure, setFailure] = useState<string | null>(null)

  const onFile = async (f: File | null) => {
    setFailure(null)
    if (!f) return onChange({ datasetFile: null, datasetMetadata: null, analyzing: false, gpuId: null, estimate: null })
    onChange({ datasetFile: f, datasetMetadata: null, analyzing: true, gpuId: null, estimate: null })
    setProgress(0)
    try {
      const meta = await analyzeDataset(f, setProgress)
      onChange({ datasetMetadata: meta, analyzing: false })
    } catch (e) {
      onChange({ analyzing: false })
      setFailure(e instanceof Error && e.message === 'EMPTY_DATASET' ? '샘플이 없는 파일입니다.' : '파일을 읽지 못했습니다. JSONL·JSON·CSV·TXT 인지 확인해 주세요.')
    }
  }

  const meta = state.datasetMetadata
  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">{copy.title}</h2>
        <p className="text-label-sm text-text-muted">{copy.desc}</p>
      </header>

      <FileDrop
        file={state.datasetFile}
        onFile={onFile}
        accept={DATASET_ACCEPT}
        label="데이터셋 파일을 끌어놓거나 클릭해 선택"
        hint="JSONL · JSON · CSV · TXT"
      />

      {/* 분석 상태 — 진행 중 / 결과 / 실패 */}
      {state.analyzing && (
        <p role="status" className="text-caption tabular-nums text-text-muted">
          분석 중… {state.datasetFile ? `${Math.min(100, Math.round((progress / Math.max(1, state.datasetFile.size)) * 100))}%` : ''}
        </p>
      )}
      {failure && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-caption text-danger">
          {failure}
        </p>
      )}
      {meta && !state.analyzing && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-lg border border-hairline bg-surface-1 p-4 text-caption tabular-nums md:grid-cols-5">
          {(
            [
              ['크기', formatBytes(meta.sizeBytes)],
              ['샘플', `${meta.sampleCount.toLocaleString('ko-KR')}개`],
              ['토큰(근사)', meta.totalTokens.toLocaleString('ko-KR')],
              ['평균 길이', String(Math.round(meta.avgSeqLength))],
              ['최대 길이', String(meta.maxSeqLength)],
            ] as [string, string][]
          ).map(([k, v]) => (
            <div key={k} className="flex flex-col">
              <dt className="text-text-muted">{k}</dt>
              <dd className="font-semibold text-dark">{v}</dd>
            </div>
          ))}
          <p className="col-span-full text-hint text-text-muted">토큰 수는 근사값 · 이 5개 값만 서버로 전송</p>
        </dl>
      )}

    </>
  )
}
