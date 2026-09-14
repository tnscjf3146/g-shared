'use client'

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  ReceiptTimeoutError,
  SSAFY_CHAIN,
  approveGsc,
  createAndFundJob,
  ensureSsafyChain,
  extractEscrowJobId,
  formatUnits,
  getGscAllowance,
  getGscBalance,
  getProvider,
  requestAccount,
  shortAddress,
  waitForReceipt,
} from '@/features/wallet/ethereum'
import { fundJob, getEscrowTerms } from '../api'
import type { JobDetail } from '../types'

// GSC 예치(0914 연동 규격) — CREATED 작업 상세에 붙는 패널. MetaMask 서명이 두 번 뜬다: 1/2 승인(approve) → 2/2 예치(createAndFundJob).
// 인자(externalRef·priceWei·providerWallet)는 전부 백엔드(escrow-terms)가 준 값 그대로 — 프론트가 만들지 않는다.
// 체인 성공 + 서버 확인을 구분한다: 서버 확인만 실패하면 escrowJobId 를 들고 재요청만 한다(새 예치 트랜잭션 금지)

type Phase =
  | { s: 'idle' }
  | { s: 'busy'; label: string } // 검사·서명 대기·채굴 대기 — 버튼 잠금
  | { s: 'reportFailed'; escrowJobId: string; txHash: string } // 체인 성공, 서버 확인만 실패
  | { s: 'error'; message: string }
  | { s: 'done' }

/** MetaMask code 4001 = 사용자가 팝업에서 거절 */
const isRejected = (e: unknown) => (e as { code?: number })?.code === 4001

export function FundPanel({ job, onFunded }: { job: JobDetail; onFunded: () => void }) {
  const [phase, setPhaseRaw] = useState<Phase>({ s: 'idle' })
  const [txHash, setTxHash] = useState<string | null>(null)
  const busy = phase.s === 'busy'

  // 예치는 서명·채굴 대기로 오래 걸린다 — 도중에 벗어나면(작업 상태 폴링으로 리렌더/언마운트) setState 를 건너뛴다
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  const setPhase = (p: Phase) => { if (alive.current) setPhaseRaw(p) }

  const report = async (escrowJobId: string, hash: string) => {
    setPhase({ s: 'busy', label: '서버 확인 중' })
    try {
      await fundJob(job.jobId, escrowJobId)
      setPhase({ s: 'done' })
      onFunded()
    } catch {
      // 체인은 성공 — 새 트랜잭션을 만들면 이중 예치가 되므로, 같은 escrowJobId 로 확인만 다시
      setPhase({ s: 'reportFailed', escrowJobId, txHash: hash })
    }
  }

  const fund = async () => {
    setTxHash(null)
    try {
      setPhase({ s: 'busy', label: '예치 조건 확인 중' })
      const terms = await getEscrowTerms(job.jobId)
      if (terms.chainId !== SSAFY_CHAIN.id) return setPhase({ s: 'error', message: '지원하지 않는 네트워크 조건입니다. 관리자에게 문의해 주세요.' })
      const price = BigInt(terms.priceWei) // Number() 금지 — 정밀도 손실

      const p = getProvider()
      if (!p) return setPhase({ s: 'error', message: 'MetaMask가 설치되어 있지 않습니다. 설치 후 다시 시도해 주세요.' })

      setPhase({ s: 'busy', label: '지갑 확인 중' })
      const account = await requestAccount(p)
      if (!account) return setPhase({ s: 'error', message: '지갑 계정을 확인하지 못했습니다. MetaMask에서 계정을 연결해 주세요.' })
      if (account.toLowerCase() !== terms.clientWallet.toLowerCase())
        return setPhase({ s: 'error', message: `연결된 지갑(${shortAddress(account)})이 계정에 등록된 지갑(${shortAddress(terms.clientWallet)})과 다릅니다. MetaMask에서 계정을 바꿔 주세요.` })
      await ensureSsafyChain(p)

      setPhase({ s: 'busy', label: '잔액 확인 중' })
      const balance = await getGscBalance(account)
      if (balance < price) return setPhase({ s: 'error', message: `GSC 잔액이 부족합니다. (보유 ${formatUnits(balance, 18, 2)} / 필요 ${formatUnits(price, 18, 2)} GSC)` })

      // 1/2 승인 — allowance 가 이미 충분하면 생략
      const allowance = await getGscAllowance(account, terms.escrowAddress)
      if (allowance < price) {
        setPhase({ s: 'busy', label: '1/2 GSC 사용 승인 — MetaMask 서명 대기' })
        const approveHash = await approveGsc(p, account, terms.escrowAddress, price)
        setPhase({ s: 'busy', label: '1/2 승인 처리 중 (블록 확인)' })
        const approveReceipt = await waitForReceipt(approveHash)
        if (approveReceipt.status !== '0x1') return setPhase({ s: 'error', message: '승인 트랜잭션이 실패했습니다. 다시 시도해 주세요.' })
      }

      // 2/2 예치
      setPhase({ s: 'busy', label: '2/2 예치 — MetaMask 서명 대기' })
      const hash = await createAndFundJob(p, account, terms.escrowAddress, terms.externalRef, price, terms.providerWallet)
      setTxHash(hash)
      setPhase({ s: 'busy', label: '2/2 예치 처리 중 (블록 확인)' })
      const receipt = await waitForReceipt(hash)
      if (receipt.status !== '0x1') return setPhase({ s: 'error', message: '예치 트랜잭션이 실패했습니다. 승인 금액이 모자라거나 이미 예치된 작업일 수 있습니다.' })

      const escrowJobId = extractEscrowJobId(receipt, terms.escrowAddress, terms.externalRef)
      if (!escrowJobId) return setPhase({ s: 'error', message: '예치 이벤트를 찾지 못했습니다. 트랜잭션 해시를 팀에 알려 주세요.' })
      await report(escrowJobId, hash)
    } catch (e) {
      if (isRejected(e)) setPhase({ s: 'error', message: '서명을 거절했습니다. 다시 시도할 수 있습니다.' })
      else if (e instanceof ReceiptTimeoutError) setPhase({ s: 'error', message: '블록 확인이 지연되고 있습니다. 잠시 후 다시 시도해 주세요. 트랜잭션을 다시 보내지 마세요.' })
      else setPhase({ s: 'error', message: '예치 처리 중 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.' })
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-primary/45 bg-primary/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-[14px] font-semibold text-dark">예치하고 학습 시작하기</span>
          <span className="text-[13px] text-text-muted">
            {job.estimatedCost.toLocaleString('ko-KR')} GSC를 에스크로 금고에 맡기면 워커 배정이 시작됩니다. MetaMask 서명이 두 번(승인·예치) 필요합니다.
          </span>
        </div>
        {phase.s !== 'reportFailed' && (
          <Button disabled={busy} onClick={fund}>
            {busy ? phase.label + '…' : phase.s === 'error' ? '다시 예치 시도' : `${job.estimatedCost.toLocaleString('ko-KR')} GSC 예치`}
          </Button>
        )}
      </div>

      {phase.s === 'error' && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[14px] text-danger">{phase.message}</p>
      )}
      {phase.s === 'reportFailed' && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-warning-tint px-4 py-3">
          <p className="text-[14px] text-warning">예치는 체인에서 처리되었습니다. 서버 확인이 지연되고 있습니다 — 트랜잭션을 다시 보내지 마세요.</p>
          <Button variant="secondary" size="sm" onClick={() => report(phase.escrowJobId, phase.txHash)}>서버 확인 재시도</Button>
        </div>
      )}
      {txHash && (
        <button
          type="button"
          title={txHash}
          onClick={() => navigator.clipboard?.writeText(txHash).catch(() => {})}
          className="self-start text-[13px] text-text-muted tabular-nums hover:text-dark hover:underline"
        >
          트랜잭션 {txHash.slice(0, 10)}… 복사
        </button>
      )}
    </div>
  )
}
