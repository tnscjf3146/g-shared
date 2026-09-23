'use client'

import { useState } from 'react'
import { STATUS_META } from '@/features/jobs/format'
import type { EscrowState, JobOnchain, JobStatus } from '@/features/jobs/types'

// 체인이 말하는 것 — DB 스냅샷이 아니라 백엔드가 RPC 로 노드에서 다시 읽은 값.
// 발표에서 "우리 장부가 아니라 체인에 이렇게 적혀 있다"를 화면이 스스로 증명해야 해서,
// 근거(블록 번호·조회 시각·출처)를 값과 같은 비중으로 보여 준다.
//
// ⚠️ 블록 탐색기 링크는 걸지 않는다 — SSAFY Blockscout 이 403 이라(RPC 는 200) 링크가 죽는다.
//    대신 txHash 복사 버튼을 둬서 "이 해시로 누구든 직접 확인할 수 있다"를 유지한다.

/** available=false 사유 → 사용자 문장. 모르는 코드는 일반 안내로 흘린다(백엔드가 사유를 늘려도 화면은 안 깨짐) */
const REASON_TEXT: Record<string, string> = {
  ESCROW_JOB_ID_MISSING: '아직 예치 전입니다. 예치가 끝나면 체인 기록이 생깁니다.',
  ESCROW_CONTRACT_MISSING: '이 작업의 에스크로 계약 주소를 찾을 수 없습니다.',
  BLOCKCHAIN_RPC_UNAVAILABLE: '체인 조회에 실패했습니다. 잠시 후 다시 시도해 주세요.',
}
const ESCROW_STATE_LABEL: Record<EscrowState, string> = {
  NONE: '기록 없음',
  FUNDED: '예치 완료',
  ASSIGNED: '작업 수락',
  COMPLETED: '완료',
  CANCELLED: '취소·환불',
}
/** 거래 종류 — 체인에 남은 순서대로 읽힌다 */
const TX_LABEL: Record<string, string> = { FUND: '예치', ACCEPT: '수락', SUBMIT: '결과 제출', ABORT: '취소' }

/** DB 상태(JobStatus) ↔ 체인 상태(EscrowJobState) 대응.
 *  둘이 같아야 정상이고 다르면 **체인이 정본**이다(백엔드 EscrowReconciler 와 같은 원칙). */
//  FAILED 도 넣는다 — 실패하면 환불되어 체인은 CANCELLED 여야 한다. 그런데 백엔드는
//  "DB 는 끝났지만 온체인 에스크로는 아직 FUNDED 일 수 있어 **사람이 봐야 한다**"는 경우를
//  명시적으로 남긴다(JobService.rejectJob 주석). 그때 돈이 묶여 있다는 걸 화면이 잡아내야 한다.
//  CREATED(아직 체인에 없음)·CANCELLING(정리 중)은 과도기라 비교하지 않는다.
const EXPECTED_STATE: Partial<Record<JobStatus, EscrowState>> = {
  FUNDED: 'FUNDED',
  ASSIGNED: 'ASSIGNED',
  RUNNING: 'ASSIGNED',
  COMPLETED: 'COMPLETED',
  CANCELLED: 'CANCELLED',
  FAILED: 'CANCELLED',
}

export function OnchainPanel({ data, dbStatus }: { data: JobOnchain | null; dbStatus: JobStatus }) {
  if (!data) return null

  if (!data.available) {
    const reason = data.reason ?? ''
    return (
      <section className="card flex flex-col gap-2">
        <Heading />
        <p className="text-caption text-text-muted">{REASON_TEXT[reason] ?? '체인 기록을 아직 불러올 수 없습니다.'}</p>
      </section>
    )
  }

  const chainState = data.onchain?.state ?? null
  const expected = EXPECTED_STATE[dbStatus]
  // 대응표에 없는 DB 상태(CREATED·CANCELLING 등)는 "아직/과도기"라 불일치로 보지 않는다
  const mismatch = expected != null && chainState != null && chainState !== expected
  const txs = data.transactions ?? []

  return (
    <section className="card flex flex-col gap-5">
      <Heading />

      {/* 체인이 말하는 상태 ↔ 우리 DB 가 말하는 상태 — 나란히 두어 같은지 바로 보이게 */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Box label="체인이 말하는 상태" value={chainState ? ESCROW_STATE_LABEL[chainState] : '—'} strong />
        <Box label="서비스가 말하는 상태" value={STATUS_META[dbStatus].label} />
      </div>
      {mismatch && (
        <p role="status" className="rounded-md bg-warning-tint px-3 py-2 text-caption text-warning">
          체인과 서비스 상태가 다릅니다. <strong>체인 기록이 정본</strong>이며, 서비스 상태는 곧 맞춰집니다.
        </p>
      )}

      {/* 체인에 적힌 값들 */}
      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {data.escrowJobId && <Row label="에스크로 Job" value={`#${data.escrowJobId}`} />}
        {data.onchain?.resultHash && <Row label="체인의 Result Hash" value={data.onchain.resultHash} mono copyable />}
        {data.chain?.contractAddress && <Row label="에스크로 계약" value={data.chain.contractAddress} mono copyable />}
        {data.chain?.chainId != null && <Row label="체인 ID" value={String(data.chain.chainId)} />}
      </dl>

      {/* 트랜잭션 — 발표에서 "이 해시로 누구든 체인에 직접 확인할 수 있다"고 말하는 근거 */}
      <div className="flex flex-col gap-2">
        <h3 className="text-caption font-semibold text-dark">체인에 남은 거래</h3>
        {txs.length === 0 ? (
          <p className="text-caption text-text-muted">아직 기록된 거래가 없습니다.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-hairline rounded-lg border border-hairline bg-surface-1">
            {txs.map((t) => (
              <li key={t.txHash} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5">
                <span className="text-caption font-semibold text-dark">{TX_LABEL[t.type ?? ''] ?? t.type ?? '거래'}</span>
                {t.blockNumber != null && (
                  <span className="text-caption tabular-nums text-text-muted">블록 #{t.blockNumber.toLocaleString('ko-KR')}</span>
                )}
                <TxStatus status={t.status} />
                <CopyValue value={t.txHash} className="ml-auto" />
              </li>
            ))}
          </ul>
        )}
      </div>

      <Evidence evidence={data.evidence} />
    </section>
  )
}

function Heading() {
  return (
    <div className="flex flex-col gap-1">
      <h2 className="text-body font-bold tracking-heading text-dark">온체인 검증</h2>
      <p className="text-caption text-text-muted">블록체인 노드에서 직접 읽은 기록입니다.</p>
    </div>
  )
}

/** 영수증이 아직 없으면 status 가 비어 온다 — "실패"로 단정하지 않는다 */
function TxStatus({ status }: { status?: string | null }) {
  if (status === 'SUCCESS') return <span className="text-caption text-success">성공</span>
  if (status === 'REVERTED') return <span className="text-caption text-danger">되돌려짐</span>
  return <span className="text-caption text-text-muted">확인 중</span>
}

function Box({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`flex flex-col gap-1 rounded-lg border p-3 ${strong ? 'border-primary/45 bg-primary/5' : 'border-hairline bg-surface-1'}`}>
      <span className="text-hint font-medium tracking-eyebrow text-text-muted">{label}</span>
      <span className="text-body font-bold text-dark">{value}</span>
    </div>
  )
}

function Row({ label, value, mono = false, copyable = false }: { label: string; value: string; mono?: boolean; copyable?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-hint font-medium tracking-eyebrow text-text-muted">{label}</dt>
      <dd className="min-w-0">
        {copyable ? <CopyValue value={value} /> : <span className={`text-caption text-dark ${mono ? 'tabular-nums' : ''}`}>{value}</span>}
      </dd>
    </div>
  )
}

/** 0x1234…abcd + 복사. 탐색기 링크를 못 거는 대신 전체 값을 손에 쥐여 준다 */
function CopyValue({ value, className = '' }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* 클립보드 불가 환경 — title 로 전체 값은 보인다 */
    }
  }
  return (
    <button type="button" onClick={copy} title={value} className={`group flex items-center gap-2 text-left ${className}`}>
      <span className="text-caption tabular-nums text-dark">{`${value.slice(0, 10)}…${value.slice(-6)}`}</span>
      <span className="rounded-xs bg-neutral-tint px-1.5 py-0.5 text-micro text-text-muted group-hover:text-dark">{copied ? '복사됨' : '복사'}</span>
    </button>
  )
}

function Evidence({ evidence }: { evidence: JobOnchain['evidence'] }) {
  if (!evidence) return null
  const block = evidence.latestBlockNumber
  const at = evidence.verifiedAt
  // source 가 BLOCKCHAIN_RPC 일 때만 "노드에서 읽었다"고 말한다 — 아니면 근거 없는 주장이 된다
  if (evidence.source !== 'BLOCKCHAIN_RPC') return null
  return (
    <p className="text-hint text-text-muted">
      블록체인 노드 조회
      {block != null && ` · 블록 #${block.toLocaleString('ko-KR')}`}
      {at && ` · ${new Date(at).toLocaleTimeString('ko-KR')}`}
    </p>
  )
}
