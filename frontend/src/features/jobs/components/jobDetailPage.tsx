'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirmDialog'
import { ApiError } from '@/lib/fetcher'
import { toastStore } from '@/lib/stores/toast-store'
import { cancelEscrowJob, ensureSsafyChain, getProvider, requestAccount, shortAddress, waitForReceipt } from '@/features/wallet/ethereum'
import { cancelJob, getCatalog, getDataset, getJob } from '../api'
import { jobMessageFor } from '../errors'
import { ACTIVE_STATUSES, CANCELLABLE_STATUSES, adapterFileName, formatBytes, formatCost, formatDateTime, formatDuration, jobStatusMeta, jobStatusSentence } from '../format'
import type { Catalog, Dataset, JobDetail } from '../types'
import { useJobRealtime } from '../use-job-realtime'
import { AdapterCard } from './adapterCard'
import { FundPanel } from './fundPanel'
import { shortWorkerId } from './jobDetailInline'
import { JobProgress } from './jobProgress'
import { JobDetailSkeleton } from './jobSkeleton'
import { TrainingResultVerificationPanel } from './trainingResultVerificationPanel'

// /jobs/[id] 본문(job.md §5) — 대시보드와 같은 톤: 배지·모션 없이 제목 줄 + 속성 카드. 진행 중 상태면 10초 폴링, 끝나면 중단. 이름(workload·모델·GPU)은 카탈로그로 id→이름
const POLL_MS = 3_000 // 실시간이 없거나 SSE 가 못 덮는 상태 — 시연 중 블록 생성(10초)보다 화면이 늦지 않게 짧게
const SLOW_POLL_MS = 15_000 // SSE 가 살아 있을 때의 안전망 — 죽은 커넥션에 화면이 얼어붙지 않게 최소한만 돈다
type TabKey = 'config' | 'dataset' | 'cost' | 'adapter' | 'verify'
// 종료 이벤트 타입 → REST 가 쓰는 상태값. 토스트 중복 제거 키를 대시보드와 같은 문자열로 맞추는 용도
const TERMINAL_STATUS = { JOB_COMPLETED: 'COMPLETED', JOB_FAILED: 'FAILED', JOB_CANCELLED: 'CANCELLED' } as const

// 한 건의 속성을 읽기 쉽게 — 표 대신 "라벨(작게·회색) 위, 값(크게·진하게)" 키-값 그리드. 테두리 대신 여백으로 구분
function DetailGrid({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-8 gap-y-5 sm:grid-cols-3 lg:grid-cols-4">
      {items.map(([k, v]) => (
        <div key={k} className="flex min-w-0 flex-col gap-1">
          <dt className="text-hint font-medium tracking-eyebrow text-text-muted">{k}</dt>
          <dd className="text-body font-semibold break-words tabular-nums text-dark">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
type State = { status: 'loading' } | { status: 'notfound' } | { status: 'error' } | { status: 'ready'; job: JobDetail }

export function JobDetailPage({ jobId }: { jobId: number }) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [dataset, setDataset] = useState<Dataset | null>(null) // GET /datasets/{id} — 상세 응답엔 메타가 없어 따로
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false) // 작업 취소 확인 대화상자
  const [tab, setTab] = useState<TabKey>('config') // 진행 아래 상세를 탭으로(설정·데이터셋·비용·어댑터)

  const load = useCallback(
    (silent = false) =>
      getJob(jobId)
        .then((job) => setState({ status: 'ready', job }))
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) setState({ status: 'notfound' })
          else if (!silent) setState({ status: 'error' })
        }),
    [jobId],
  )

  const active = state.status === 'ready' && ACTIVE_STATUSES.has(state.job.status)
  useEffect(() => {
    void load()
  }, [load])

  // 실시간 구독 — 진행 중일 때만. 이벤트가 오면 그 즉시 반영하고, 종료 이벤트는 REST 로 한 번 더 맞춘다
  // (진행률·상태 말고도 결과물·해시 같은 게 같이 채워지므로 상세를 다시 받는 편이 안전하다)
  const realtime = useJobRealtime(jobId, active, load)
  const ev = realtime.event
  // 종료 이벤트는 물론, **이벤트가 말하는 상태가 지금 화면과 다르면** 상세를 다시 받는다.
  // (진행률 말고도 결과물·해시가 같이 채워지고, 놓친 전이를 여기서 따라잡는다)
  const evStatus = ev?.status ?? null
  const shownStatus = state.status === 'ready' ? state.job.status : null
  // 조건을 "상태가 다를 때"로 통일한다 — 종료 이벤트에도 같은 조건을 걸어야 load 가 두 번 나가지 않는다
  // (첫 load 로 shownStatus 가 바뀌면 이 effect 가 다시 도는데, 그때는 이미 같아져 멈춘다)
  useEffect(() => {
    if (!ev || evStatus == null) return
    if (evStatus !== shownStatus) void load(true)
  }, [ev, evStatus, shownStatus, load])

  // 종료 알림 — 재연결·재조회로 같은 종료를 다시 관측해도 `jobId + 상태` 로 1회만(스펙 §8).
  // 실패 사유(reason)는 내부 코드라 그대로 노출하지 않는다 — 상세 화면이 사람이 읽을 문장을 따로 보여 준다
  //
  // 키는 **이벤트 타입이 아니라 상태값**(`COMPLETED`)으로 만든다. 대시보드는 REST 목록의
  // `status` 로 같은 키를 만드는데, 여기서 `JOB_COMPLETED` 를 쓰면 문자열이 달라
  // 두 화면 사이 중복 제거가 조용히 깨진다(지금은 라우트가 배타적이라 안 드러날 뿐이다).
  // `status` 는 스키마상 없을 수도 있어(optional·nullable) 없으면 타입에서 직접 만든다 —
  // 이벤트 타입을 그대로 폴백으로 쓰면 바로 그 어긋난 키가 되살아난다
  useEffect(() => {
    if (!ev || ev.type === 'JOB_PROGRESS') return
    const dedupeKey = `job:${ev.jobId}:${ev.status ?? TERMINAL_STATUS[ev.type]}`
    if (ev.type === 'JOB_COMPLETED') {
      // 링크를 달지 않는다 — 이 토스트는 **그 작업의 상세 화면에서만** 뜨므로
      // "결과 보기"가 지금 보고 있는 페이지를 가리키게 된다. 결과는 아래 탭에 이미 나타난다
      toastStore.push({ tone: 'success', message: '학습이 완료되었습니다. 아래에서 결과를 확인할 수 있습니다.', dedupeKey })
    } else if (ev.type === 'JOB_FAILED') {
      toastStore.push({ tone: 'danger', message: '학습 작업이 완료되지 않았습니다. 상세 상태를 확인해 주세요.', dedupeKey })
    } else {
      toastStore.push({ tone: 'neutral', message: '작업이 취소되었습니다.', dedupeKey })
    }
  }, [ev])
  // 완료 이벤트가 왔는데 REST 가 아직 RUNNING 을 돌려주는 짧은 틈이 있다. 그때 진행률이
  // 100% 에서 옛 값으로 뚝 떨어져 보이면 안 되므로 100 으로 고정해 둔다(다음 응답이 오면 바가 사라진다)
  const livePercent =
    ev?.type === 'JOB_COMPLETED' ? 100 : ev?.type === 'JOB_PROGRESS' && ev.progress != null ? Math.round(ev.progress) : null
  // 백엔드가 스텝을 줄 때만 — 없으면 줄 자체를 안 그린다
  const liveStep = ev?.type === 'JOB_PROGRESS' && ev.currentStep != null && ev.totalSteps != null ? `${ev.currentStep} / ${ev.totalSteps}` : null

  // 폴링을 **끄지 않고 간격만 늦춘다.** 두 가지 이유가 겹친다:
  //  ① SSE 가 알려주는 건 RUNNING 중의 진행률과 종료뿐이다 — 백엔드에 FUNDED→ASSIGNED,
  //     ASSIGNED→RUNNING 전이 이벤트가 없다(progress·completed·failed·cancelled 4종).
  //  ② 백엔드 SSE 는 `new SseEmitter(0L)`(무한 타임아웃)에 heartbeat 가 없다. 네트워크가 조용히
  //     죽으면(와이파이 전환·프록시 idle kill) read() 가 TCP 타임아웃까지 매달려 connected 는
  //     true 인 채 이벤트만 끊긴다. 그때 폴링까지 꺼져 있으면 **진행률이 그대로 얼어붙는다.**
  //  → RUNNING + 연결됨이면 느리게(15초), 그 밖에는 빠르게(3초). 어느 경우에도 화면이 멈추지 않는다
  const sseLive = realtime.connected && state.status === 'ready' && state.job.status === 'RUNNING'
  const pollMs = sseLive ? SLOW_POLL_MS : POLL_MS
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') void load(true)
    }, pollMs)
    return () => clearInterval(id)
  }, [active, pollMs, load])
  useEffect(() => {
    getCatalog()
      .then(setCatalog)
      .catch(() => {})
  }, [])
  const datasetId = state.status === 'ready' ? state.job.datasetId : null
  useEffect(() => {
    if (datasetId == null) return
    getDataset(datasetId)
      .then(setDataset)
      .catch(() => {}) // 못 받으면 데이터셋 표만 비운다
  }, [datasetId])

  if (state.status === 'loading') return <JobDetailSkeleton />
  if (state.status === 'notfound') {
    return (
      <section className="card flex w-full max-w-[640px] flex-col items-start gap-3 p-10">
        <p className="text-label-sm text-dark">작업을 찾을 수 없습니다.</p>
        <Link href="/dashboard/jobs" className="text-caption text-primary hover:underline">
          ← 돌아가기
        </Link>
      </section>
    )
  }
  if (state.status === 'error') {
    return (
      <section className="card flex w-full max-w-[640px] flex-col items-start gap-3 p-10">
        <p className="text-label-sm text-dark">작업 정보를 불러오지 못했습니다.</p>
        <button
          type="button"
          onClick={() => {
            setState({ status: 'loading' })
            void load()
          }}
          className="text-caption text-primary hover:underline"
        >
          다시 시도 →
        </button>
      </section>
    )
  }

  const { job } = state
  const meta = jobStatusMeta(job.status, job.failReason, job.cancelReason)
  const name = {
    workload: catalog?.workloads.find((w) => w.id === job.workloadId)?.name ?? `#${job.workloadId}`,
    model: catalog?.baseModels.find((m) => m.id === job.baseModelId)?.name ?? `#${job.baseModelId}`,
  }
  // 예치 전에 올라갔어야 할 파일이 빠졌는가 — 데이터셋과 입력 어댑터 둘 다 검증돼야 /fund 가 열린다
  // 이어서 학습(parentJobId)은 어댑터가 이미 서버에 있어 업로드 대상이 아니다 — 올렸어야 하는 건 .tar 를 고른 경우뿐
  const missingUpload = job.datasetUploaded === false ? '데이터셋' : job.hasAdapter && job.parentJobId == null && job.adapterUploaded === false ? '어댑터' : null
  // 어댑터를 만드는 작업인가. 카탈로그를 못 받았으면 결과물 유무로 대신 판단한다(카탈로그 실패로 다운로드가 사라지면 안 된다)
  const workloadKind = catalog?.workloads.find((w) => w.id === job.workloadId)?.kind
  const isTrain = workloadKind ? workloadKind === 'TRAIN_LORA' : job.adapterName != null || job.resultUrl != null || job.resultHash != null

  // 진행 아래 상세 탭 — 데이터셋은 받았을 때만, 어댑터/결과 탭은 결과물이 있는 작업만
  const hasResultTab = isTrain || (job.status === 'COMPLETED' && !!(job.resultUrl || job.resultHash))
  const tabs: { key: TabKey; label: string }[] = [
    { key: 'config', label: '설정' },
    ...(dataset ? [{ key: 'dataset' as TabKey, label: '데이터셋' }] : []),
    { key: 'cost', label: '비용 · 시간' },
    ...(hasResultTab ? [{ key: 'adapter' as TabKey, label: isTrain ? '어댑터' : '결과' }] : []),
    // 학습 검증 — 카탈로그로 확정된 학습(LoRA) 워크로드만. isTrain 의 결과물 휴리스틱(카탈로그 실패 대비)을
    // 여기에 재사용하면 비학습 작업에도 탭이 뜨고 검증 API 를 불필요하게 부른다. 완료 전에는 패널이 "완료 후 확인" 안내
    ...(workloadKind === 'TRAIN_LORA' ? [{ key: 'verify' as TabKey, label: '학습 검증' }] : []),
  ]
  const activeTab: TabKey = tabs.some((t) => t.key === tab) ? tab : 'config'

  // FUNDED 는 에스크로 금고에 GSC 가 잠겨 있다 — 온체인 cancelJob 으로 환불을 먼저 받고(MetaMask 서명 1회),
  // 그 다음 기존 취소 API 로 서버 상태를 맞춘다. 그 외 상태(CREATED·ASSIGNED)는 서버 취소만
  const cancelOnChain = async () => {
    // 예치 정보는 상세 응답에 실려 온다. 예치가 끝난 Job 은 예치 당시 컨트랙트(escrowContractAddress)가 정본
    const escrowAddress = job.escrowContractAddress ?? job.escrowAddress
    if (!job.escrowJobId || !escrowAddress || !job.clientWallet) throw new Error('온체인 예치 정보를 찾을 수 없습니다. 새로고침 후에도 반복되면 팀에 문의해 주세요.')
    const p = getProvider()
    if (!p) throw new Error('MetaMask가 설치되어 있지 않습니다.')
    const account = await requestAccount(p)
    if (account.toLowerCase() !== job.clientWallet.toLowerCase())
      throw new Error(`연결된 지갑(${shortAddress(account)})이 예치한 지갑(${shortAddress(job.clientWallet)})과 다릅니다.`)
    await ensureSsafyChain(p)
    const hash = await cancelEscrowJob(p, account, escrowAddress, BigInt(job.escrowJobId))
    const receipt = await waitForReceipt(hash)
    if (receipt.status !== '0x1') throw new Error('환불 트랜잭션이 실패했습니다. 이미 수락된 작업일 수 있습니다.')
  }

  const funded = job.status === 'FUNDED'
  const cancel = async () => {
    setConfirmOpen(false)
    setPending(true)
    setError(null)
    try {
      if (funded) await cancelOnChain()
      await cancelJob(job.jobId)
      await load()
    } catch (e) {
      if ((e as { code?: number })?.code === 4001) setError('서명을 거절했습니다. 다시 시도할 수 있습니다.')
      else if (e instanceof Error && !(e instanceof ApiError) && e.message) setError(e.message)
      else setError(jobMessageFor(e))
      if (e instanceof ApiError && e.status === 409) void load(true) // 상태가 이미 바뀜 → 최신으로
    } finally {
      setPending(false)
    }
  }

  const dash = '—'
  return (
    <div className="flex w-full flex-col gap-6">
      {/* 헤더: [← 돌아가기] 한 줄, 그 아래 워크로드 이름 · 상태(글자) | 작업 취소 */}
      <header className="flex flex-col gap-3">
        <Link href="/dashboard/jobs" className="self-start text-label-sm text-text-muted hover:text-dark">
          ← 돌아가기
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 flex-wrap items-center gap-4">
            <h1 className={`min-w-0 break-words text-h1 font-bold tracking-heading ${job.status === 'CANCELLED' ? 'text-text-muted line-through decoration-2' : 'text-dark'}`}>{name.workload}</h1>
            <span className={`text-body font-medium ${meta.tone === 'danger' ? 'text-danger' : job.status === 'CANCELLED' ? 'text-text-muted line-through decoration-1' : 'text-dark'}`}>{meta.label}</span>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" href={`/jobs/${job.jobId}/chain`}>
              블록체인 거래 흐름 보기 →
            </Button>
            {CANCELLABLE_STATUSES.has(job.status) && (
              <Button variant="secondary" size="sm" disabled={pending} onClick={() => setConfirmOpen(true)}>
                {pending ? '취소 중…' : '작업 취소'}
              </Button>
            )}
          </div>
        </div>
      </header>
      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-label-sm text-danger">
          {error}
        </p>
      )}

      <section className="card flex flex-col gap-3 px-6 py-5">
        <h2 className="text-body font-bold tracking-heading text-dark">진행</h2>
        <p className={`text-label-sm ${meta.tone === 'danger' ? 'text-danger' : 'text-dark'}`}>{jobStatusSentence(job.status, job.failReason, job.cancelReason)}</p>
        {job.status === 'FAILED' && job.failReason && <p className="rounded-md bg-danger-tint px-4 py-3 text-caption break-all text-danger">{job.failReason}</p>}
        {job.status === 'CREATED' &&
          (missingUpload ? (
            // 등록 도중 업로드가 끊긴 작업 — 서버는 /fund 를 409 DATASET_NOT_UPLOADED / ADAPTER_NOT_UPLOADED 로 거절하고, 상세에는 재업로드가 없다(파일은 브라우저 메모리에만 있었다)
            <p role="alert" className="rounded-md bg-warning-tint px-4 py-3 text-caption text-warning">
              {missingUpload} 파일 업로드가 끝나지 않아 예치할 수 없습니다. 위의 [작업 취소]로 이 작업을 닫고 워크로드 등록을 다시 진행해 주세요.
            </p>
          ) : (
            <FundPanel job={job} onFunded={() => void load()} />
          ))}
        {/* 진행률은 이벤트가 오면 그 값이 먼저다 — REST 응답을 기다리지 않고 즉시 움직인다.
            JobProgress 는 대시보드와 공용이라 시그니처를 건드리지 않고 넘기는 값만 덮어쓴다 */}
        {job.status === 'RUNNING' && (
          <>
            <JobProgress job={livePercent != null ? { ...job, progressPercent: livePercent } : job} />
            {liveStep && <p className="text-caption tabular-nums text-text-muted">Step {liveStep}</p>}
          </>
        )}
      </section>

      {/* 상세는 탭으로 — 진행 아래에서 설정·데이터셋·비용·어댑터를 하나씩 (스크롤 대신). 진행 카드와 간격을 둔다 */}
      <div className="mt-3">
        <div role="tablist" aria-label="작업 상세" className="flex gap-2 overflow-x-auto pb-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              id={`jobtab-${t.key}`}
              type="button"
              role="tab"
              aria-selected={activeTab === t.key}
              aria-controls="jobtab-panel"
              tabIndex={activeTab === t.key ? 0 : -1} // 로빙 tabindex — 활성 탭만 Tab 으로 들어오고, 좌우 화살표로 이동
              onClick={() => setTab(t.key)}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
                e.preventDefault()
                const i = tabs.findIndex((x) => x.key === activeTab)
                const nextKey = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length].key
                setTab(nextKey)
                document.getElementById(`jobtab-${nextKey}`)?.focus()
              }}
              // 탭 필(~36h)은 버튼 스케일을 따른다 — btn-sm 32h/13 · btn-md 40h/14 사이라 14(caption).
              // 이전 15px 은 btn-lg(46h) 라벨 크기여서 컨트롤 높이에 비해 컸다
              className={`flex-none rounded-md px-4 py-2 text-caption font-semibold transition-colors ${activeTab === t.key ? 'bg-primary text-white shadow-2' : 'bg-surface-1 text-text-muted hover:bg-black/5 hover:text-dark'}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div id="jobtab-panel" role="tabpanel" aria-labelledby={`jobtab-${activeTab}`} className="pt-4">
          {activeTab === 'config' && (
            <section className="card flex flex-col gap-3 px-6 py-5">
              <DetailGrid
                items={[
                  ['작업 종류', name.workload],
                  ['베이스 모델', name.model],
                  ['학습 설정', job.trainingMode === 'AUTO' ? '자동' : '직접 설정'],
                  // 배정 워커는 진행 카드 아래 별도 줄이었다 — 값 하나짜리 줄을 없애고 같은 성격의 속성들과 한 격자에 모은다
                  ...(job.assignedWorkerId ? ([['배정 워커', <span key="w" title={job.assignedWorkerId}>{shortWorkerId(job.assignedWorkerId)}</span>]] as [string, React.ReactNode][]) : []),
                  ['에폭', String(job.epochs)],
                  ['배치 크기', String(job.batchSize)],
                  ['학습률', String(job.learningRate)],
                  ['최대 길이', String(job.maxLength)],
                ]}
              />
              {/* 입력 어댑터(이어서 학습·업로드) — 예치 전 업로드 여부까지 한 줄로 */}
              <p className="text-caption text-text-muted">
                입력 어댑터{' '}
                <span className="font-medium text-dark">
                  {/* adapterUploaded 는 끝난 작업의 파일을 cleanup 이 지우면 다시 false 가 된다 — "업로드 필요"는 예치 전(CREATED)에만 맞는 말 */}
                  {!job.hasAdapter ? '없음 (처음부터 학습)' : job.parentJobId != null ? `작업 #${job.parentJobId} 에서 이어서` : job.status === 'CREATED' && job.adapterUploaded === false ? '업로드 필요' : '업로드됨'}
                </span>
              </p>
            </section>
          )}

          {/* 데이터셋 — 3단계에서 브라우저가 뽑은 메타 + 파일 해시. GET /datasets/{datasetId}(파일명은 서버에 없다) */}
          {activeTab === 'dataset' && dataset && (
            <section className="card flex flex-col gap-3 px-6 py-5">
              <DetailGrid
                items={[
                  ['크기', formatBytes(dataset.sizeBytes)],
                  ['샘플', `${dataset.sampleCount.toLocaleString('ko-KR')}개`],
                  ['토큰(근사)', dataset.totalTokens.toLocaleString('ko-KR')],
                  ['유효 토큰', dataset.effectiveTokens.toLocaleString('ko-KR')],
                  ['평균 길이', String(Math.round(dataset.avgSeqLength))],
                  ['최대 길이', String(dataset.maxSeqLength)],
                  ['파일 해시', dataset.dataHash ? <span title={dataset.dataHash}>{dataset.dataHash.slice(0, 12)}…</span> : dash],
                ]}
              />
            </section>
          )}

          {activeTab === 'cost' && (
            <section className="card flex flex-col gap-3 px-6 py-5">
              <DetailGrid
                items={[
                  ['예상 비용', formatCost(job.estimatedCost)],
                  ['실제 비용', job.actualCost != null ? formatCost(job.actualCost) : dash],
                  ['예상 소요', formatDuration(job.predictedRuntimeSec)],
                  ['실제 소요', job.actualRuntimeSec != null ? formatDuration(job.actualRuntimeSec) : dash],
                  ['생성', formatDateTime(job.createdAt)],
                  ['시작', job.startedAt ? formatDateTime(job.startedAt) : dash],
                  ['종료', job.finishedAt ? formatDateTime(job.finishedAt) : dash],
                ]}
              />
              {job.finishedAt == null && (
                <p className="text-caption text-text-muted">
                  예상 소요는 순수 학습 시간입니다. 매칭 대기와 워커의 실행 환경 준비(이미지·데이터 다운로드)는 포함되지 않아, 특히 첫 실행은 10분 이상 더 걸릴 수 있어요.
                </p>
              )}
            </section>
          )}

          {/* 어댑터 — 학습 결과물. 어댑터를 만들지 않는 워크로드(추론·평가)는 결과 파일만 */}
          {activeTab === 'adapter' &&
            (isTrain ? (
              <AdapterCard job={job} onRenamed={(next) => setState({ status: 'ready', job: next })} />
            ) : (
              <section className="card flex flex-col gap-3 px-6 py-5">
                <div className="flex flex-wrap items-center gap-3">
                  {job.resultUrl && (
                    <a href={job.resultUrl} download={adapterFileName(job.jobId, `${name.workload}-job-${job.jobId}`, null, job.resultUrl)} rel="noreferrer" className="btn btn-md btn-primary">
                      결과 다운로드
                    </a>
                  )}
                  {job.resultHash && (
                    <button type="button" title={job.resultHash} onClick={() => navigator.clipboard?.writeText(job.resultHash!).catch(() => {})} className="btn btn-md btn-secondary tabular-nums">
                      해시 {job.resultHash.slice(0, 10)}… 복사
                    </button>
                  )}
                </div>
              </section>
            ))}

          {activeTab === 'verify' && <TrainingResultVerificationPanel job={job} />}
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        message={funded ? `작업 #${job.jobId} 을(를) 취소할까요? 예치한 GSC 가 지갑으로 환불됩니다. (MetaMask 서명 1회)` : `작업 #${job.jobId} 을(를) 취소할까요?`}
        confirmLabel="작업 취소"
        danger
        pending={pending}
        onConfirm={cancel}
        onCancel={() => !pending && setConfirmOpen(false)}
      />
    </div>
  )
}
