'use client'

import { useEffect, useState } from 'react'
import { ApiError } from '@/lib/fetcher'
import { getJobResultVerification } from '../api'
import { formatDuration } from '../format'
import type { JobDetail, ResultVerification } from '../types'
import { LossChart } from './lossChart'
import { LoraWeightHeatmap } from './loraWeightHeatmap'

// 학습 결과 검증 패널(Training_Result_Verification_Panel_Guide) — COMPLETED 만 API 를 부르고,
// 섹션(요약·Loss·Adapter·Heatmap·무결성)은 각자 빈 상태를 가진다: 한 조각이 없어도 패널 전체가 죽지 않는다.
// 검증 API 실패는 "정보를 못 불러온 것"이지 작업 실패가 아니다 — 문구도 그렇게 쓴다

// forJob: 이 상태가 어느 작업의 응답인지 — jobId 가 바뀌면 렌더에서 로딩으로 취급해
// 이전 작업의 검증 hash 를 새 작업의 resultHash 와 비교하는 거짓 "불일치"를 막는다
type State = { s: 'loading' } | { s: 'error'; forJob: number } | { s: 'ready'; v: ResultVerification; forJob: number }

/** 상단 요약 숫자 한 칸 — 값 크게, 라벨 작게 */
function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[22px] font-bold tabular-nums text-dark">{value}</span>
      <span className="text-[12px] font-medium tracking-eyebrow text-text-muted">{label}</span>
    </div>
  )
}

const shortHash = (h: string) => (h.length > 18 ? `${h.slice(0, 8)}…${h.slice(-6)}` : h)
/** 5.77초처럼 초 미만이 의미 있는 짧은 실행은 소수 2자리, 그 외는 기존 포맷("1시간 13분") */
const runtime = (sec: number) => (sec < 60 ? `${sec.toFixed(2)}초` : formatDuration(sec))

export function TrainingResultVerificationPanel({ job }: { job: JobDetail }) {
  const completed = job.status === 'COMPLETED'
  const [state, setState] = useState<State>({ s: 'loading' })

  const [retry, setRetry] = useState(0) // "다시 시도" 버튼 → effect 재실행
  useEffect(() => {
    if (!completed) return // COMPLETED 가 아니면 요청 자체를 보내지 않는다(가이드 §19)
    // 늦게 도착한 이전 요청의 응답은 버린다(ignore). 동기 setState 는 린트 금지라
    // "로딩 리셋"은 렌더 쪽에서 forJob ≠ 현재 jobId 를 로딩으로 취급하는 것으로 대신한다
    let ignore = false
    const forJob = job.jobId
    getJobResultVerification(forJob)
      .then((v) => {
        if (!ignore) setState({ s: 'ready', v, forJob })
      })
      // 404 = 이 작업(또는 아직 API 가 없는 서버)에 검증 데이터가 없다 — 에러가 아니라 legacy 빈 상태.
      // 백엔드 배포 전 실서버에서 이 탭이 "고장"처럼 보이지 않게 한다
      .catch((e) => {
        if (!ignore) setState(e instanceof ApiError && e.status === 404 ? { s: 'ready', v: { available: false }, forJob } : { s: 'error', forJob })
      })
    return () => {
      ignore = true
    }
  }, [completed, job.jobId, retry])
  // 현재 jobId 의 응답이 아니면(방금 다른 작업으로 이동) 아직 로딩 중인 것
  const view: State = state.s === 'loading' || state.forJob === job.jobId ? state : { s: 'loading' }

  if (!completed) {
    return (
      <section className="card px-6 py-5">
        <p className="text-[15px] text-text-muted">학습 결과는 작업 완료 후 확인할 수 있습니다.</p>
      </section>
    )
  }
  if (view.s === 'loading') {
    return (
      <section className="card flex flex-col gap-3 px-6 py-5" aria-busy>
        <div className="h-6 w-40 animate-pulse rounded bg-surface-1" />
        <div className="h-[170px] animate-pulse rounded bg-surface-1" />
      </section>
    )
  }
  if (view.s === 'error') {
    return (
      <section className="card flex flex-col items-start gap-3 px-6 py-5">
        <p className="text-[15px] text-dark">학습 결과 검증 정보를 불러오지 못했습니다.</p>
        <button
          type="button"
          onClick={() => {
            setState({ s: 'loading' })
            setRetry((n) => n + 1)
          }}
          className="text-[14px] text-primary hover:underline"
        >
          다시 시도 →
        </button>
      </section>
    )
  }

  const { v } = view
  if (!v.available) {
    return (
      <section className="card px-6 py-5">
        <p className="text-[15px] text-text-muted">이 작업은 상세 학습 검증 데이터를 제공하지 않습니다.</p>
      </section>
    )
  }

  const t = v.training
  const a = v.adapter
  // 배지는 adapter.valid 기준 — null(판단 불가)이면 배지를 달지 않는다. "품질 검증"이 아니라 구조·수치 확인이다
  const badge = a?.valid === true ? { label: '정상 완료', cls: 'bg-primary/12 text-primary-dark' } : a?.valid === false ? { label: '검증 이상 발견', cls: 'bg-danger-tint text-danger' } : null
  // 무결성: 검증 API 의 결과 파일 hash ↔ 상세 응답의 resultHash(워커가 체인에 제출한 값)
  const backendHash = v.integrity?.resultHash ?? null
  const chainHash = job.resultHash
  const hashMatch = backendHash != null && chainHash != null ? backendHash.toLowerCase() === chainHash.toLowerCase() : null

  return (
    <section className="card flex flex-col gap-6 px-6 py-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-[17px] font-bold tracking-heading text-dark">학습 결과 검증</h2>
          <p className="text-[13px] text-text-muted">LoRA Adapter 구조 및 Weight Update 확인</p>
        </div>
        {badge && <span className={`rounded-full px-3 py-1 text-[13px] font-semibold ${badge.cls}`}>{badge.label}</span>}
      </header>

      {/* 상단 요약 4칸 */}
      {t ? (
        <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
          <Stat value={t.sampleCount.toLocaleString('ko-KR')} label="Samples" />
          <Stat value={String(t.totalSteps)} label="Training Steps" />
          <Stat value={runtime(t.actualRuntimeSec)} label="Runtime" />
          <Stat value={t.finalLoss.toFixed(3)} label="Final Loss" />
        </div>
      ) : (
        <p className="text-[14px] text-text-muted">학습 요약 데이터가 제공되지 않습니다.</p>
      )}

      {/* Loss Curve | Adapter 검증 — 데스크톱 2열, 모바일 세로 */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="card-quiet flex flex-col gap-2 p-4">
          <h3 className="text-[14px] font-semibold text-dark">Loss Curve</h3>
          <LossChart history={t?.lossHistory ?? []} />
        </div>
        <div className="card-quiet flex flex-col gap-3 p-4">
          <h3 className="text-[14px] font-semibold text-dark">LoRA Adapter 검증</h3>
          {a ? (
            <>
              <div className="grid grid-cols-2 gap-x-6 gap-y-4">
                <Stat value={a.tensorCount.toLocaleString('ko-KR')} label="LoRA Tensors" />
                <Stat value={a.changedTensorCount.toLocaleString('ko-KR')} label="Changed" />
                {/* NaN·Inf 는 0 이 정상 — 0 이 아니면 숫자 자체를 빨갛게(색+텍스트 병행) */}
                <Stat value={String(a.nanCount)} label={a.nanCount > 0 ? 'NaN (비정상)' : 'NaN'} />
                <Stat value={String(a.infCount)} label={a.infCount > 0 ? 'Inf (비정상)' : 'Inf'} />
              </div>
              <p className={`text-[13px] ${a.valid === false || a.nanCount > 0 || a.infCount > 0 ? 'text-danger' : 'text-text-muted'}`}>
                {a.valid === false
                  ? '검증 이상 발견 — tensor 수치에 비정상 값이 있습니다.'
                  : `${a.changedTensorCount.toLocaleString('ko-KR')}/${a.tensorCount.toLocaleString('ko-KR')} tensor 가 학습으로 변경됐고, 비정상 수치(NaN·Inf)는 없습니다.`}
              </p>
            </>
          ) : (
            <p className="text-[14px] text-text-muted">Adapter 검증 데이터가 제공되지 않습니다.</p>
          )}
        </div>
      </div>

      {/* Layer × Module heatmap */}
      <div className="flex flex-col gap-2">
        <h3 className="text-[14px] font-semibold text-dark">Layer별 LoRA Weight 변화</h3>
        <LoraWeightHeatmap data={a?.layerModuleDeltaMagnitude ?? {}} />
      </div>

      {/* 결과 무결성 — 검증 hash ↔ 체인 제출 hash. 이 조각만 실패해도 위 섹션은 그대로 */}
      <div className="flex flex-col gap-2 border-t border-hairline pt-4">
        <h3 className="text-[14px] font-semibold text-dark">결과 무결성</h3>
        {backendHash == null ? (
          <p className="text-[14px] text-text-muted">결과 Hash 정보가 제공되지 않습니다.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <span className="text-[14px] tabular-nums text-dark" title={backendHash}>
              <span className="text-text-muted">Backend </span>
              {shortHash(backendHash)}
            </span>
            <span className="text-[14px] tabular-nums text-dark" title={chainHash ?? undefined}>
              <span className="text-text-muted">Chain </span>
              {chainHash ? shortHash(chainHash) : '—'}
            </span>
            {hashMatch == null ? (
              <span className="text-[13px] text-text-muted">Blockchain 기록 hash 가 제공되지 않습니다.</span>
            ) : (
              <span className={`rounded-full px-2.5 py-0.5 text-[13px] font-semibold ${hashMatch ? 'bg-primary/12 text-primary-dark' : 'bg-danger-tint text-danger'}`}>{hashMatch ? '결과 Hash 일치' : '결과 Hash 불일치'}</span>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
