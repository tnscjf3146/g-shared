'use client'

import Link from 'next/link'
import { formatUnits } from '@/features/wallet/ethereum'
import { AUTO_MODE_META, adapterLabel, formatBytes, formatCost } from '../format'
import { ONE_GSC, hasEnough, type Funds } from '../hooks/use-wallet-funds'
import type { Catalog, JobSpec } from '../types'
import { EstimateCard } from './estimateCard'
import { chosenAdapterFile, chosenParent, resultAdapterName, resultAdapterVersion, type WizardState } from './workloadWizard'

// 5단계: 업로드 — 선택 요약 + 4단계 견적 + 지갑 잔액 확인(job.md §4). 잔액 상태(funds)는 위저드가 들고 있고(하단 [업로드] 버튼 활성 판단), 여기선 보여 주기만.
// 잔액 부족이면 [예치금 예치하기], 지갑이 없으면 [지갑 연결하기] + [지갑 연결 확인]. 파일(데이터셋·어댑터)은 하단 업로드 버튼을 눌러야 서버로 간다
type Props = { catalog: Catalog; spec: JobSpec; state: WizardState; funds: Funds; onRecheck: () => void }

export function StepUpload({ catalog, spec, state, funds, onRecheck }: Props) {

  const workload = catalog.workloads.find((w) => w.id === state.workloadId)
  const model = catalog.baseModels.find((m) => m.id === state.baseModelId)
  // 4단계에서 고르는 순간 적어 둔 이름을 그대로 쓴다 — /gpus 를 따로 받지 않고, 목록 도착 전에도 "—" 가 되지 않는다
  const dataset = state.datasetFile ? `${state.datasetFile.name} (${formatBytes(state.datasetFile.size)})` : undefined
  const meta = spec.datasetMetadata
  const isTrain = workload?.kind === 'TRAIN_LORA'
  // 어댑터: 출발점(이어서 학습할 작업 또는 올릴 .tar)과 결과물 이름 — 결과 버전은 서버가 확정하므로 아는 경우에만 붙인다
  const parent = chosenParent(state)
  const adapterFile = chosenAdapterFile(state)
  const source = parent ? `${adapterLabel(parent.jobId, parent.name, parent.version)} (작업 #${parent.jobId}) 이어서` : adapterFile ? `${adapterFile.name} (${formatBytes(adapterFile.size)})` : isTrain ? '처음부터' : '없음'
  const resultName = resultAdapterName(state, model?.name) // 2단계 안내·전송과 같은 값
  const resultVersion = resultAdapterVersion(state)

  const cost = state.estimate?.estimatedCost ?? 0
  const needWei = BigInt(Math.ceil(cost)) * ONE_GSC
  const enough = hasEnough(funds, cost)
  const shortage = funds.status === 'ready' && !enough ? needWei - funds.balance : null

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">업로드</h2>
        <p className="text-label-sm text-text-muted">등록 내용을 확인하세요. GPU 배정과 최종 견적은 서버가 확정합니다.</p>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-lg border border-hairline bg-surface-1 p-5 text-caption">
        {(
          [
            ['워크로드', workload?.name],
            // 이름이 이미 크기를 말하면 괄호를 붙이지 않는다 — "Qwen3 0.6B (600M)" 은 같은 값을 두 번 쓴 것이다(FE-11, 1단계와 같은 규칙)
            ['모델', model && (model.params ? `${model.name} (${model.params})` : model.name)],
            [isTrain ? '학습 시작점' : '어댑터', source],
            ...(isTrain ? ([['만들어질 어댑터', `${resultName}${resultVersion ? ` v${resultVersion}` : ''}`]] as [string, string][]) : []),
            ['설정', workload?.kind === 'TRAIN_LORA' ? (typeof state.custom === 'object' && state.custom ? '직접 설정' : `자동 · ${AUTO_MODE_META[state.level].label}`) : '—'],
            ['데이터셋', dataset],
            ['데이터셋 분석', `샘플 ${meta.sampleCount.toLocaleString('ko-KR')}개 · 약 ${meta.totalTokens.toLocaleString('ko-KR')} 토큰 · 평균 ${Math.round(meta.avgSeqLength)} · 최대 ${meta.maxSeqLength}`],
            ['GPU 등급', state.gpuLabel ?? '—'],
          ] as [string, string | undefined][]
        ).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-text-muted">{k}</dt>
            <dd className="font-medium text-dark">{v ?? '—'}</dd>
          </div>
        ))}
      </dl>

      {/* 지갑 잔액 vs 예상 비용 */}
      <section className="flex flex-col gap-3 rounded-lg border border-hairline bg-surface-1 p-5" aria-live="polite">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-label-sm font-semibold text-dark">지갑 잔액</h3>
          {funds.status === 'ready' && (
            <button type="button" onClick={onRecheck} className="text-caption text-primary hover:underline">
              잔액 다시 확인
            </button>
          )}
        </div>
        {funds.status === 'loading' && <div aria-busy aria-label="잔액 확인 중" className="h-6 w-48 animate-pulse rounded-xs bg-neutral-tint" />}
        {funds.status === 'error' && (
          <p role="alert" className="text-caption text-danger">
            잔액을 확인하지 못했습니다.{' '}
            <button type="button" onClick={onRecheck} className="underline">
              다시 시도
            </button>
          </p>
        )}
        {funds.status === 'nowallet' && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-caption text-dark">연결된 지갑이 없습니다. 비용은 지갑의 GSC 로 예치됩니다.</p>
            <div className="flex gap-2">
              {/* 새 탭에서 연결하고 돌아오면 [지갑 연결 확인]으로 이 화면만 다시 확인 */}
              <button type="button" onClick={onRecheck} className="btn btn-md btn-secondary">
                지갑 연결 확인
              </button>
              <Link href="/wallet" target="_blank" rel="noreferrer" className="btn btn-md btn-primary">
                지갑 연결하기
              </Link>
            </div>
          </div>
        )}
        {funds.status === 'ready' && (
          <div className="flex flex-wrap items-end justify-between gap-4">
            <dl className="flex gap-8 text-caption">
              <div className="flex flex-col">
                <dt className="text-text-muted">보유</dt>
                <dd className={`text-body font-bold tabular-nums ${enough ? 'text-dark' : 'text-danger'}`}>{formatUnits(funds.balance)} GSC</dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-muted">예상 비용</dt>
                <dd className="text-body font-bold tabular-nums text-dark">{formatCost(cost)}</dd>
              </div>
              {shortage !== null && (
                <div className="flex flex-col">
                  <dt className="text-text-muted">부족</dt>
                  <dd className="text-body font-bold tabular-nums text-danger">{formatUnits(shortage)} GSC</dd>
                </div>
              )}
            </dl>
            {!enough && (
              <Link href={`/wallet?deposit=${Math.ceil(cost)}`} target="_blank" rel="noreferrer" className="btn btn-lg btn-primary">
                예치금 예치하기
              </Link>
            )}
          </div>
        )}
        {funds.status === 'ready' && !enough && <p className="text-hint text-text-muted">GSC 충전 후 [잔액 다시 확인] · 선택한 내용은 유지됩니다</p>}
      </section>

      {state.estimate && <EstimateCard estimate={state.estimate} stale={false} />}
      <p className="text-caption text-text-muted">
        데이터셋{adapterFile ? '과 어댑터 파일은' : '은'} 등록 직후 서버에 올라가 해시를 검증한 뒤 작업에 사용됩니다.
        {isTrain ? ' 어댑터 이름은 등록 뒤에도 작업 상세에서 바꿀 수 있고, 다운로드 파일 이름이 됩니다.' : ''}
      </p>
    </>
  )
}
