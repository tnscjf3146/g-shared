'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { getWallet } from '@/features/wallet/api'
import { formatUnits, getGscBalance } from '@/features/wallet/ethereum'
import { AUTO_MODE_META, formatBytes, formatCost } from '../format'
import type { Catalog, JobSpec } from '../types'
import { EstimateCard } from './estimateCard'
import type { WizardState } from './workloadWizard'

// 5단계: 업로드 — 선택 요약 + 4단계 견적 + 지갑 잔액 확인(job.md §4).
// 지갑 GSC 잔액 ≥ 예상 비용이면 [업로드하고 등록], 부족하면 [예치금 예치하기], 지갑이 없으면 [지갑 연결하기]. 파일은 업로드 버튼을 눌러야 서버로 간다
const ONE_GSC = BigInt(10) ** BigInt(18) // GSC decimals 18

type Funds = { status: 'loading' } | { status: 'nowallet' } | { status: 'error' } | { status: 'ready'; address: string; balance: bigint }
type Props = { catalog: Catalog; spec: JobSpec; state: WizardState; submitting: boolean; onSubmit: () => void }

export function StepUpload({ catalog, spec, state, submitting, onSubmit }: Props) {
  const [funds, setFunds] = useState<Funds>({ status: 'loading' })

  // 지갑 주소(백엔드) → GSC 잔액(SSAFY 공개 RPC). 응답이 온 뒤에만 setState
  const check = useCallback(
    () =>
      getWallet()
        .then(({ walletAddress }) => (walletAddress ? getGscBalance(walletAddress).then((balance) => setFunds({ status: 'ready', address: walletAddress, balance })) : setFunds({ status: 'nowallet' })))
        .catch(() => setFunds({ status: 'error' })),
    [],
  )
  useEffect(() => {
    void check()
  }, [check])

  const workload = catalog.workloads.find((w) => w.id === state.workloadId)
  const model = catalog.baseModels.find((m) => m.id === state.baseModelId)
  const gpu = catalog.gpus.find((g) => g.id === state.gpuId)
  const dataset = state.datasetFile ? `${state.datasetFile.name} (${formatBytes(state.datasetFile.size)})` : catalog.datasets.find((d) => d.id === state.datasetId)?.name
  const meta = spec.datasetMetadata
  const uploads = [state.datasetFile && '데이터셋', state.adapterFile && '어댑터', state.ownModelFile && '내 모델'].filter(Boolean).join(' · ')

  const cost = state.estimate?.estimatedCost ?? 0
  const needWei = BigInt(Math.ceil(cost)) * ONE_GSC
  const enough = funds.status === 'ready' && funds.balance >= needWei
  const shortage = funds.status === 'ready' && !enough ? needWei - funds.balance : null

  return (
    <>
      <header className="flex flex-col gap-1">
        <h2 className="text-h2 font-bold tracking-heading text-dark">5. 업로드</h2>
        <p className="text-[15px] text-text-muted">아래 내용으로 워크로드를 등록합니다. 지갑에 예상 비용만큼 GSC 가 있어야 업로드할 수 있습니다.</p>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 rounded-lg border border-hairline bg-surface-1 p-5 text-[14px]">
        {(
          [
            ['워크로드', workload?.name],
            ['모델', state.modelSource === 'OWN' ? `내 모델 · ${state.ownModelFile?.name}` : `${model?.name} (${model?.params})`],
            ['어댑터', state.adapterFile?.name ?? '없음'],
            ['설정', workload?.kind === 'TRAIN_LORA' ? (typeof state.custom === 'object' && state.custom ? '직접 설정' : `자동 · ${AUTO_MODE_META[state.level].label}`) : '—'],
            ['데이터셋', dataset],
            ['데이터셋 분석', `샘플 ${meta.sampleCount.toLocaleString('ko-KR')}개 · 약 ${meta.totalTokens.toLocaleString('ko-KR')} 토큰 · 평균 ${Math.round(meta.avgSeqLength)} · 최대 ${meta.maxSeqLength}`],
            ['GPU 등급', gpu ? `${gpu.model.replace('NVIDIA GeForce ', '')} · ${Math.round(gpu.vramMb / 1024)} GB` : '—'],
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
          <h3 className="text-[15px] font-semibold text-dark">지갑 잔액</h3>
          {funds.status === 'ready' && (
            <button type="button" onClick={() => { setFunds({ status: 'loading' }); void check() }} className="text-[13px] text-primary hover:underline">
              잔액 다시 확인
            </button>
          )}
        </div>
        {funds.status === 'loading' && <div aria-busy aria-label="잔액 확인 중" className="h-6 w-48 animate-pulse rounded-xs bg-neutral-tint" />}
        {funds.status === 'error' && (
          <p role="alert" className="text-[14px] text-danger">
            잔액을 확인하지 못했습니다.{' '}
            <button type="button" onClick={() => { setFunds({ status: 'loading' }); void check() }} className="underline">
              다시 시도
            </button>
          </p>
        )}
        {funds.status === 'nowallet' && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-[14px] text-dark">연결된 지갑이 없습니다. 비용은 지갑의 GSC 로 예치됩니다.</p>
            <Link href="/wallet" target="_blank" rel="noreferrer" className="btn btn-md btn-primary">
              지갑 연결하기
            </Link>
          </div>
        )}
        {funds.status === 'ready' && (
          <div className="flex flex-wrap items-end justify-between gap-4">
            <dl className="flex gap-8 text-[14px]">
              <div className="flex flex-col">
                <dt className="text-text-muted">보유</dt>
                <dd className={`text-[18px] font-bold tabular-nums ${enough ? 'text-dark' : 'text-danger'}`}>{formatUnits(funds.balance)} GSC</dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-text-muted">예상 비용</dt>
                <dd className="text-[18px] font-bold tabular-nums text-dark">{formatCost(cost)}</dd>
              </div>
              {shortage !== null && (
                <div className="flex flex-col">
                  <dt className="text-text-muted">부족</dt>
                  <dd className="text-[18px] font-bold tabular-nums text-danger">{formatUnits(shortage)} GSC</dd>
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
        {funds.status === 'ready' && !enough && <p className="text-[12px] text-text-muted">지갑에 GSC 를 채운 뒤 [잔액 다시 확인]을 누르면 업로드 버튼이 열립니다. 이 화면의 선택은 유지됩니다.</p>}
      </section>

      {state.estimate && <EstimateCard estimate={state.estimate} stale={false} pending={submitting} onCreate={enough ? onSubmit : undefined} createLabel={uploads ? `업로드하고 등록 (${uploads})` : '등록'} />}
    </>
  )
}
