'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { walletMessageFor } from '@/features/wallet/errors'
import { ReceiptTimeoutError, ensureSsafyChain, getProvider, requestAccount, setOperator, shortAddress, waitForReceipt } from '@/features/wallet/ethereum'
import { getOperatorStatus } from '../api'
import type { OperatorStatus, Worker, WorkerStatus } from '../types'

// 워커 카드 상태 칸 — 백엔드 가이드 "Worker Operator 승인 UX"(2026-09-16) 그대로.
// 작업 가능 = ONLINE AND currentJob 없음 AND operator 승인 완료. ONLINE 만으로는 작업 가능이 아니다(§2).
// 승인 상태는 GET /workers/{id}/operator-status(§5). 승인은 공급자 MetaMask 로 setOperator(operator, true) 1회(§6), 서명 계정 = providerAddress 여야 하고(§7),
// txHash 만으로 승인 처리하지 않고 영수증 뒤 백엔드를 다시 조회해 approved=true 를 확인한다(§8). 개인키는 어디에도 두지 않는다(§10)
type ApprovalState = 'OPERATOR_APPROVAL_REQUIRED' | 'OPERATOR_APPROVAL_PENDING' | 'OPERATOR_APPROVED' | 'OPERATOR_APPROVAL_FAILED'
type Approval = { s: ApprovalState; detail?: string } // PENDING: 진행 문구 / FAILED: 원인

const CONNECTION: Record<WorkerStatus, string> = { ONLINE: '온라인', RUNNING: '작업 중', OFFLINE: '오프라인' }
const WAITING_SERVER = '트랜잭션은 확인됐지만 서버 반영을 기다리고 있습니다'

export type WorkAvailability = { text: string; tone: 'ok' | 'danger' | 'muted' }

export function OperatorApproval({ worker, onWork }: { worker: Worker; onWork?: (w: WorkAvailability) => void }) {
  const [status, setStatus] = useState<OperatorStatus | null | 'error'>(null) // null = 조회 중
  const [approval, setApprovalRaw] = useState<Approval>({ s: 'OPERATOR_APPROVAL_REQUIRED' })

  // 서명·채굴 대기가 길다 — 폴링 리렌더로 카드가 바뀌어 언마운트되면 setState 를 건너뛴다(fundPanel 과 같은 방식).
  // setup 에서 true 로 되돌려야 한다 — StrictMode(dev)는 effect 를 mount→cleanup→mount 로 두 번 돌리는데, cleanup 만 있으면 false 로 굳어 setState 가 전부 무시된다
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const setApproval = (a: Approval) => {
    if (alive.current) setApprovalRaw(a)
  }

  /** 백엔드 승인 상태 조회 — 최초 1회와 영수증 뒤 재확인(§8) */
  const refresh = useCallback(
    (): Promise<OperatorStatus | null> =>
      getOperatorStatus(worker.workerId).then(
        (st) => {
          if (alive.current) {
            setStatus(st)
            if (st.approved) setApprovalRaw({ s: 'OPERATOR_APPROVED' })
          }
          return st
        },
        () => {
          if (alive.current) setStatus('error')
          return null
        },
      ),
    [worker.workerId],
  )
  useEffect(() => {
    void refresh()
  }, [refresh])

  const approve = async () => {
    if (status === null || status === 'error' || !status.providerAddress || !status.operatorAddress || !status.escrowAddress) return
    const p = getProvider()
    if (!p) return setApproval({ s: 'OPERATOR_APPROVAL_FAILED', detail: 'MetaMask가 설치되어 있지 않습니다. 설치 후 다시 시도해 주세요.' })
    try {
      setApproval({ s: 'OPERATOR_APPROVAL_PENDING', detail: '지갑 확인 중' })
      const account = await requestAccount(p)
      // §7 connectedWallet == providerAddress. setOperator 는 msg.sender 기준으로 기록되므로 다른 계정이면 보내지 않는다
      if (account.toLowerCase() !== status.providerAddress.toLowerCase())
        return setApproval({ s: 'OPERATOR_APPROVAL_FAILED', detail: `이 GPU를 등록한 공급자 지갑으로 연결해 주세요. (현재 ${shortAddress(account)} · 필요 ${shortAddress(status.providerAddress)})` })
      await ensureSsafyChain(p) // §7 Chain ID 31221 — 다른 네트워크면 SSAFY Chain 전환 요청
      setApproval({ s: 'OPERATOR_APPROVAL_PENDING', detail: 'MetaMask 승인 요청' })
      const hash = await setOperator(p, account, status.escrowAddress, status.operatorAddress, true) // §6 setOperator(operator, true)
      setApproval({ s: 'OPERATOR_APPROVAL_PENDING', detail: '승인 처리 중' })
      const receipt = await waitForReceipt(hash)
      if (receipt.status !== '0x1') return setApproval({ s: 'OPERATOR_APPROVAL_FAILED', detail: '승인 트랜잭션이 실패했습니다. 다시 시도해 주세요.' })
      setApproval({ s: 'OPERATOR_APPROVAL_PENDING', detail: '승인 상태 확인 중' })
      const fresh = await refresh() // §8 txHash 로 끝내지 않는다 — 백엔드 approved=true 까지
      if (!fresh?.approved) setApproval({ s: 'OPERATOR_APPROVAL_PENDING', detail: WAITING_SERVER })
    } catch (e) {
      if (e instanceof ReceiptTimeoutError) setApproval({ s: 'OPERATOR_APPROVAL_FAILED', detail: '블록 확인이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.' })
      else setApproval({ s: 'OPERATOR_APPROVAL_FAILED', detail: walletMessageFor(e) }) // MetaMask reject(4001) 등 → FAILED
    }
  }

  // 실행 권한 줄 — 백엔드 status(아니오의 종류)와 승인 진행 상태로 결정
  const ready = status !== null && status !== 'error' && status.status !== 'UNVERIFIED' && !!status.providerAddress && !!status.operatorAddress && !!status.escrowAddress
  const permission = (() => {
    if (status === null) return { text: '확인 중…', tone: 'muted' as const }
    if (status === 'error') return { text: '확인 실패', tone: 'muted' as const }
    if (status.status === 'PROVIDER_WALLET_MISSING' || !status.providerAddress) return { text: '지갑 연결 필요', tone: 'danger' as const }
    if (status.status === 'OPERATOR_MISSING' || !status.operatorAddress) return { text: '워커 키 미등록 · 워커 프로그램을 최신 버전으로 실행해 주세요', tone: 'danger' as const }
    if (status.status === 'UNVERIFIED' && approval.s !== 'OPERATOR_APPROVED') return { text: '확인 불가 · 잠시 후 다시 확인해 주세요', tone: 'muted' as const }
    if (approval.s === 'OPERATOR_APPROVED') return { text: '승인 완료', tone: 'ok' as const }
    if (approval.s === 'OPERATOR_APPROVAL_PENDING') return { text: `${approval.detail}…`, tone: 'muted' as const }
    if (approval.s === 'OPERATOR_APPROVAL_FAILED') return { text: '승인 실패', tone: 'danger' as const }
    return { text: '승인 필요', tone: 'danger' as const }
  })()
  const canApprove = ready && (approval.s === 'OPERATOR_APPROVAL_REQUIRED' || approval.s === 'OPERATOR_APPROVAL_FAILED')
  const available = worker.status === 'ONLINE' && !worker.currentJobId && approval.s === 'OPERATOR_APPROVED'
  const tone = { muted: 'text-text-muted', danger: 'text-danger', ok: 'text-dark' }[permission.tone]
  // "상태" 줄은 항상 표시(카드마다 줄 수가 달라 헷갈리던 것 통일). 작업 가능 아니면 왜 안 되는지 한 단어로
  // 좁은 표 칸에서 안 잘리게 이유는 짧게 — 색(초록 가능 / 빨강 조치 필요 / 회색 대기)으로 구분하고 title 에 전체 문구
  const work = available
    ? { text: '작업 가능', tone: 'ok' as const }
    : worker.status === 'OFFLINE'
      ? { text: '오프라인', tone: 'muted' as const }
      : worker.currentJobId
        ? { text: '작업 중', tone: 'muted' as const }
        : approval.s !== 'OPERATOR_APPROVED'
          ? { text: '권한 필요', tone: 'danger' as const }
          : { text: '대기 불가', tone: 'muted' as const }
  // 작업 가능/불가는 아래 표(현재 작업 옆)에서 보여주도록 부모로 올린다 — work 는 매 렌더 새 객체라 값(text/tone)이 바뀔 때만 전달
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    onWork?.(work)
  }, [onWork, work.text, work.tone])

  return (
    <div className="flex min-w-0 flex-col gap-1 text-[14px]">
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
        <dt className="text-text-muted">연결 상태</dt>
        <dd className="truncate font-medium text-dark">{CONNECTION[worker.status]}</dd>
        <dt className="text-text-muted">실행 권한</dt>
        <dd className={`truncate font-medium ${tone}`} title={permission.text}>
          {permission.text}
        </dd>
      </dl>
      {/* §9 공급자에겐 실제 원인을 그대로 */}
      {worker.status === 'ONLINE' && ready && approval.s === 'OPERATOR_APPROVAL_REQUIRED' && <p className="text-[13px] text-danger">GPU가 온라인 상태지만 실행 권한 승인이 필요합니다.</p>}
      {approval.s === 'OPERATOR_APPROVAL_FAILED' && approval.detail && (
        <p role="alert" className="text-[13px] text-danger">
          {approval.detail}
        </p>
      )}
      {((approval.s === 'OPERATOR_APPROVAL_PENDING' && approval.detail === WAITING_SERVER) || (status !== null && status !== 'error' && status.status === 'UNVERIFIED')) && (
        <button type="button" onClick={() => void refresh()} className="self-start text-[13px] text-primary hover:underline">
          다시 확인
        </button>
      )}
      {canApprove && (
        <Button variant="secondary" size="sm" className="self-start" onClick={approve}>
          GPU 실행 권한 승인
        </Button>
      )}
    </div>
  )
}
