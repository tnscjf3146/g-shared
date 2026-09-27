'use client'

import { useEffect, useState } from 'react'
import { ApiError } from '@/lib/fetcher'
import { getJobOnchain, getJobResultVerification } from '../api'
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
      <span className="text-h2 font-bold tabular-nums text-dark">{value}</span>
      <span className="text-hint font-medium tracking-eyebrow text-text-muted">{label}</span>
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
        // JOB_NOT_FOUND 는 진짜 에러다 — 그것까지 빈 상태로 덮으면 "없는 작업"이 "검증 데이터만 없는 작업" 처럼 보인다(FE-04)
        const legacyEmpty = e instanceof ApiError && e.status === 404 && e.code !== 'JOB_NOT_FOUND'
        if (!ignore) setState(legacyEmpty ? { s: 'ready', v: { available: false }, forJob } : { s: 'error', forJob })
      })
    return () => {
      ignore = true
    }
  }, [completed, job.jobId, retry])
  // 체인이 말하는 resultHash — DB 스냅샷(job.resultHash)이 아니라 백엔드가 RPC 로 다시 읽은 값.
  // 이 조회가 실패해도 검증 패널은 그대로 떠야 하므로 따로 간다(스펙 §13). 실패·미가용이면 null.
  // ⚠️ 위 검증 조회와 같은 forJob 방어가 필요하다 — 안 하면 작업을 전환할 때 이전 작업의 체인 hash 와
  //    현재 작업의 backend hash 를 비교해 거짓 "불일치"가 뜬다(이전 리뷰 차단 사항과 같은 레이스)
  const [chain, setChain] = useState<{ hash: string | null; forJob: number } | null>(null)
  useEffect(() => {
    if (!completed) return
    let ignore = false
    const forJob = job.jobId
    getJobOnchain(forJob)
      .then((o) => {
        if (!ignore) setChain({ hash: (o.available && o.onchain?.resultHash) || null, forJob })
      })
      .catch(() => {
        if (!ignore) setChain({ hash: null, forJob })
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
        <p className="text-label-sm text-text-muted">학습 결과는 작업 완료 후 확인할 수 있습니다.</p>
      </section>
    )
  }
  if (view.s === 'loading') {
    return (
      <section className="card flex flex-col gap-3 px-6 py-5" aria-busy>
        <div className="h-6 w-40 animate-pulse rounded bg-surface-1" />
        {/* 상세는 접힌 채로 뜨므로 차트 높이(170px)를 잡아 둘 이유가 없다 — 로드 뒤 위로 튀는 것 방지 */}
        <div className="h-[72px] animate-pulse rounded bg-surface-1" />
      </section>
    )
  }
  if (view.s === 'error') {
    return (
      <section className="card flex flex-col items-start gap-3 px-6 py-5">
        <p className="text-label-sm text-dark">학습 결과 검증 정보를 불러오지 못했습니다.</p>
        <button
          type="button"
          onClick={() => {
            setState({ s: 'loading' })
            setRetry((n) => n + 1)
          }}
          className="text-caption text-primary hover:underline"
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
        <p className="text-label-sm text-text-muted">이 작업은 상세 학습 검증 데이터를 제공하지 않습니다.</p>
      </section>
    )
  }

  const t = v.training
  const a = v.adapter
  // 배지는 adapter.valid 기준 — null(판단 불가)이면 배지를 달지 않는다. "품질 검증"이 아니라 구조·수치 확인이다
  const badge = a?.valid === true ? { label: '정상 완료', cls: 'bg-primary/12 text-primary-dark' } : a?.valid === false ? { label: '검증 이상 발견', cls: 'bg-danger-tint text-danger' } : null
  // 무결성: 결과 파일 hash ↔ 체인에 적힌 hash.
  // 비교 상대는 **체인에서 읽은 값**이어야 "체인과 대조했다"고 말할 수 있다. 체인을 못 읽었을 때만
  // DB 에 남은 값(워커가 제출한 값)으로 물러서고, 그때는 라벨로 출처를 정직하게 밝힌다.
  const backendHash = v.integrity?.resultHash ?? null
  const fromChain = chain?.forJob === job.jobId ? chain.hash : null
  const chainHash = fromChain ?? job.resultHash
  const chainHashLabel = fromChain ? 'Chain' : '워커가 제출한 Hash'
  // 판정은 **체인에서 읽은 값이 있을 때만** 한다. 체인을 못 읽으면 비교 상대가 DB 의 같은 값(워커가 제출한 hash)이라
  // 자기 자신과 견주는 꼴이 돼 언제나 "일치" 가 나왔다 — 체인이 죽어 있을수록 화면은 더 확신에 차 보였다(수정요청서 FE-04).
  // 그래서 이때는 참/거짓 대신 "확인 불가" 로 둔다. 해시 두 줄은 그대로 보여 주되 출처 라벨로 구분한다.
  const hashMatch = backendHash != null && fromChain != null ? backendHash.toLowerCase() === fromChain.toLowerCase() : null

  return (
    <section className="card flex flex-col gap-6 px-6 py-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-body font-bold tracking-heading text-dark">학습 결과 검증</h2>
          <p className="text-caption text-text-muted">LoRA Adapter 구조 및 Weight Update 확인</p>
        </div>
        {badge && <span className={`rounded-full px-3 py-1 text-caption font-semibold ${badge.cls}`}>{badge.label}</span>}
      </header>

      {/* 상단 요약 4칸 */}
      {t ? (
        <div className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
          {/* 백엔드는 Runtime 이 안 준 값을 null 로 보낸다 — 빠진 칸만 —, 패널은 유지 */}
          <Stat value={t.sampleCount != null ? t.sampleCount.toLocaleString('ko-KR') : '—'} label="Samples" />
          <Stat value={t.totalSteps != null ? String(t.totalSteps) : '—'} label="Training Steps" />
          <Stat value={t.actualRuntimeSec != null ? runtime(t.actualRuntimeSec) : '—'} label="Runtime" />
          <Stat value={t.finalLoss != null ? t.finalLoss.toFixed(3) : '—'} label="Final Loss" />
        </div>
      ) : (
        <p className="text-caption text-text-muted">학습 요약 데이터가 제공되지 않습니다.</p>
      )}

      {/* 상세 셋은 접어 둔다 — 상단 4칸과 무결성만으로 "됐는지" 는 이미 보이고,
          세 그래프를 늘 펼쳐 두면 상세 화면이 필요 이상으로 길어진다.
          네이티브 <details> 를 쓴다(stepConfig 의 고급 설정과 같은 방식) — 상태·키보드·스크린리더가 공짜다 */}
      {/* items-start: 한쪽만 펼치면 stretch 가 닫힌 쪽까지 같은 높이로 늘려, 글자 한 줄짜리 빈 상자가 생긴다(리뷰 지적) */}
      <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-2">
        <details className="card-quiet p-4">
          <summary className="cursor-pointer marker:text-text-muted">
            <h3 className="inline text-caption font-semibold text-dark">Loss Curve</h3>
          </summary>
          <div className="pt-3">
            <LossChart history={t?.lossHistory ?? []} />
          </div>
        </details>
        <details className="card-quiet p-4">
          <summary className="cursor-pointer marker:text-text-muted">
            <h3 className="inline text-caption font-semibold text-dark">LoRA Adapter 검증</h3>
          </summary>
          <div className="flex flex-col gap-3 pt-3">
            {a ? (
              <>
                <div className="grid grid-cols-2 gap-x-6 gap-y-4">
                  <Stat value={a.tensorCount != null ? a.tensorCount.toLocaleString('ko-KR') : '—'} label="LoRA Tensors" />
                  <Stat value={a.changedTensorCount != null ? a.changedTensorCount.toLocaleString('ko-KR') : '—'} label="Changed" />
                  {/* NaN·Inf 는 0 이 정상 — 0 이 아니면 숫자 자체를 빨갛게(색+텍스트 병행) */}
                  <Stat value={a.nanCount != null ? String(a.nanCount) : '—'} label={(a.nanCount ?? 0) > 0 ? 'NaN (비정상)' : 'NaN'} />
                  <Stat value={a.infCount != null ? String(a.infCount) : '—'} label={(a.infCount ?? 0) > 0 ? 'Inf (비정상)' : 'Inf'} />
                </div>
                <p className={`text-caption ${a.valid === false || (a.nanCount ?? 0) > 0 || (a.infCount ?? 0) > 0 ? 'text-danger' : 'text-text-muted'}`}>
                  {a.valid === false
                    ? '검증 이상 발견 — tensor 수치에 비정상 값이 있습니다.'
                    : a.changedTensorCount != null && a.tensorCount != null
                      ? `${a.changedTensorCount.toLocaleString('ko-KR')}/${a.tensorCount.toLocaleString('ko-KR')} tensor 가 학습으로 변경됐고, 비정상 수치(NaN·Inf)는 없습니다.`
                      : '구조 검증에서 비정상 수치(NaN·Inf)는 발견되지 않았습니다.'}
                </p>
              </>
            ) : (
              <p className="text-caption text-text-muted">Adapter 검증 데이터가 제공되지 않습니다.</p>
            )}
          </div>
        </details>
      </div>

      {/* Layer × Module heatmap */}
      <details className="card-quiet p-4">
        <summary className="cursor-pointer marker:text-text-muted">
            <h3 className="inline text-caption font-semibold text-dark">Layer별 LoRA Weight 변화</h3>
          </summary>
        <div className="pt-3">
          <LoraWeightHeatmap data={a?.layerModuleDeltaMagnitude ?? {}} />
        </div>
      </details>

      {/* 결과 무결성 — 검증 hash ↔ 체인 제출 hash. 이 조각만 실패해도 위 섹션은 그대로 */}
      <div className="flex flex-col gap-2 border-t border-hairline pt-4">
        <h3 className="text-caption font-semibold text-dark">결과 무결성</h3>
        {backendHash == null ? (
          <p className="text-caption text-text-muted">결과 Hash 정보가 제공되지 않습니다.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <span className="text-caption tabular-nums text-dark" title={backendHash}>
              <span className="text-text-muted">Backend </span>
              {shortHash(backendHash)}
            </span>
            {/* 체인을 못 읽었을 때는 이 줄을 아예 그리지 않는다 — 내용이 Backend 줄과 바이트까지 같은 값이라,
                출처만 다른 라벨을 달아 두면 서로 다른 두 증거를 맞춰 본 것처럼 보인다(리뷰 지적) */}
            {fromChain && (
              <span className="text-caption tabular-nums text-dark" title={fromChain}>
                <span className="text-text-muted">{chainHashLabel} </span>
                {shortHash(fromChain)}
              </span>
            )}
            {hashMatch == null ? (
              <span className="text-caption text-text-muted">{backendHash != null && chainHash != null ? '체인 기록을 읽지 못해 대조하지 못했습니다 — 확인 불가' : 'Blockchain 기록 hash 가 제공되지 않습니다.'}</span>
            ) : (
              <span className={`rounded-full px-2.5 py-0.5 text-caption font-semibold ${hashMatch ? 'bg-primary/12 text-primary-dark' : 'bg-danger-tint text-danger'}`}>{hashMatch ? '결과 Hash 일치' : '결과 Hash 불일치'}</span>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
