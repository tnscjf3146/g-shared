'use client'

import { useState } from 'react'
import { FileDrop } from '@/components/ui/fileDrop'
import { analyzeDataset } from '../dataset-analysis'
import { formatBytes } from '../format'
import type { Catalog, WorkloadKind } from '../types'
import type { WizardState } from './workloadWizard'

// 3단계: 데이터셋 — 파일 끌어놓기/클릭. 파일은 서버로 안 가고 **브라우저에서 분석**해 메타데이터 5값만 뽑는다(job.md §4-3)
const DATASET_ACCEPT = '.jsonl,.json,.csv,.txt'
// 화면 표기는 accept 에서 파생한다 — 두 곳에 적으면 형식을 늘릴 때 안내만 조용히 어긋난다
const DATASET_ACCEPT_LABEL = DATASET_ACCEPT.split(',')
  .map((e) => e.slice(1).toUpperCase())
  .join(' · ')
// 제목만. 형식 안내는 아래 FileDrop 이 받을 수 있는 확장자로 이미 말해 준다
const TITLE: Record<WorkloadKind, string> = {
  TRAIN_LORA: '학습 데이터셋',
  INFERENCE: '입력 데이터',
  EVALUATION: '평가 데이터셋',
}

type Props = { catalog: Catalog; state: WizardState; onChange: (p: Partial<WizardState>) => void }

export function StepDataset({ catalog, state, onChange }: Props) {
  const kind = catalog.workloads.find((w) => w.id === state.workloadId)?.kind ?? 'TRAIN_LORA'
  const title = TITLE[kind]
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
        <h2 className="text-h2 font-bold tracking-heading text-dark">{title}</h2>
      </header>

      <FileDrop
        file={state.datasetFile}
        onFile={onFile}
        accept={DATASET_ACCEPT}
        label="데이터셋 파일을 끌어놓거나 클릭해 선택"
        hint={DATASET_ACCEPT_LABEL}
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
