'use client'

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  ReceiptTimeoutError,
  SSAFY_CHAIN,
  approveGsc,
  createAndFundJob,
  ensureSsafyChain,
  formatUnits,
  getGscAllowance,
  getGscBalance,
  getProvider,
  requestAccount,
  shortAddress,
  waitForReceipt,
} from '@/features/wallet/ethereum'
import { fundJob } from '../api'
import { isTransientFundError, jobMessageFor } from '../errors'
import type { JobDetail } from '../types'

// GSC 예치(Escrow_Fund_계약_정리 2026-09-15) — CREATED 작업 상세에 붙는 패널. MetaMask 서명이 두 번 뜬다: 1/2 승인(approve) → 2/2 예치(createAndFundJob).
// 예치 입력값(externalRef·priceWei·providerWallet·escrowAddress 등)은 JobDetail 응답에 실려 온다 — 프론트가 만들거나 계산하지 않는다.
// 서버 확인은 txHash 만 보낸다(온체인 jobId 는 백엔드가 영수증에서 읽음). 멱등이라 확인 실패·PENDING 이면 같은 txHash 로 재시도만(새 트랜잭션 금지)

type Phase =
  | { s: 'idle' }
  | { s: 'busy'; label: string } // 검사·서명 대기·채굴 대기 — 버튼 잠금
  | { s: 'pendingConfirm'; txHash: string; message: string } // 체인 성공, 서버 확인 대기(PENDING)·일시 실패 — 같은 해시로 재시도
  | { s: 'rejected'; txHash: string; message: string } // 체인 성공, 서버가 확정 거절(4xx) — 재시도도 재예치도 안 된다. 해시를 남기고 팀 문의
  | { s: 'error'; message: string }
  | { s: 'done' }

/** MetaMask code 4001 = 사용자가 팝업에서 거절 */
const isRejected = (e: unknown) => (e as { code?: number })?.code === 4001

/**
 * 서버가 기억하는 예치 해시(`fundTxHash`)로 보류 상태를 만든다.
 *
 * <p>예전에는 보류가 이 컴포넌트의 state 에만 있어서 새로고침하면 `idle` 로 돌아갔다. 그러면 예치
 * 버튼이 다시 보이고, 두 번째 서명은 컨트랙트가 같은 externalRef 라며 되돌려 "실패" 문구로 끝났다.
 * 돈이 두 번 나가지는 않지만 사용자는 실패했다고 믿게 되고, 그 상태로 취소하면 온체인 자금 추적이
 * 끊긴다(수정요청서 FE-01 → BE-01). 서버는 이미 해시를 들고 있으니 그것을 정본으로 쓴다.
 */
const PENDING_MESSAGE = '예치는 체인에 접수됐고 서버가 영수증을 기다리는 중입니다. 트랜잭션을 다시 보내지 말고 아래에서 확인만 다시 눌러 주세요.'
const pendingPhase = (hash: string): Phase => ({ s: 'pendingConfirm', txHash: hash, message: PENDING_MESSAGE })

export function FundPanel({ job, onFunded }: { job: JobDetail; onFunded: () => void }) {
  const [localPhase, setPhaseRaw] = useState<Phase>({ s: 'idle' })
  const [localTxHash, setTxHash] = useState<string | null>(null)

  // 서버가 해시를 들고 있으면 그게 정본이다 — state 로 **복사해 두지 않고** 렌더에서 겹쳐 쓴다.
  // 복사하면 두 가지를 다 놓친다: 마운트 때만 읽으면(초기값) 폴링으로 뒤늦게 온 해시를 영영 못 보고
  // (상세 화면은 CREATED 동안 3초마다 job prop 만 갈아 끼울 뿐 이 컴포넌트를 다시 마운트하지 않는다),
  // effect 로 맞추면 같은 상태를 두 군데에 두는 동기화가 된다. 파생이면 둘 다 없다.
  // 내 흐름이 진행 중일 때(busy·done·rejected)는 건드리지 않는다 — 서명 중 화면이 바뀌면 안 된다
  const phase: Phase = job.fundTxHash && (localPhase.s === 'idle' || localPhase.s === 'error') ? pendingPhase(job.fundTxHash) : localPhase
  const txHash = localTxHash ?? job.fundTxHash ?? null
  const busy = phase.s === 'busy'

  // 예치는 서명·채굴 대기로 오래 걸린다 — 도중에 벗어나면(작업 상태 폴링으로 리렌더/언마운트) setState 를 건너뛴다
  // setup 에서 true 로 되돌린다 — StrictMode(dev)는 effect 를 mount→cleanup→mount 로 두 번 돌려, cleanup 만 있으면 false 로 굳어 setPhase 가 전부 무시됐다(2026-09-15)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  const setPhase = (p: Phase) => { if (alive.current) setPhaseRaw(p) }

  // 서버 확인 — 멱등이라 같은 txHash 로 몇 번이고 안전. FUNDED 면 완료, PENDING 이면 재시도 안내
  const report = async (hash: string) => {
    setPhase({ s: 'busy', label: '서버 확인 중' })
    try {
      const res = await fundJob(job.jobId, hash)
      if (res.status === 'FUNDED') {
        setPhase({ s: 'done' })
        onFunded()
      } else {
        setPhase({ s: 'pendingConfirm', txHash: hash, message: '예치는 체인에 접수됐고 서버가 영수증을 기다리는 중입니다. 잠시 후 다시 확인해 주세요.' })
      }
    } catch (e) {
      console.error('예치 서버 확인 실패', e) // 체인은 성공 — 원인 파악용 로그
      if (isTransientFundError(e)) setPhase({ s: 'pendingConfirm', txHash: hash, message: '예치는 체인에서 처리되었습니다. 서버 확인이 지연되고 있습니다 — 트랜잭션을 다시 보내지 마세요.' })
      else setPhase({ s: 'rejected', txHash: hash, message: `${jobMessageFor(e)} 예치 트랜잭션은 체인에 남아 있습니다 — 아래 해시와 함께 팀에 문의해 주세요.` })
    }
  }

  const fund = async () => {
    setTxHash(null)
    let sentHash: string | null = null // catch 에서 state(txHash)는 이 클로저 기준 옛 값 — 지역 변수로 추적
    try {
      // 예치 입력값은 상세 응답에 실려 온다 — 백엔드가 아직 안 실어 주면(구버전) 진행 불가 안내
      const { externalRef, priceWei, providerWallet, clientWallet, escrowAddress, chainId } = job
      if (!clientWallet) return setPhase({ s: 'error', message: '계정에 연결된 지갑이 없습니다. 지갑 페이지에서 먼저 연결해 주세요.' })
      if (!externalRef || !priceWei || !providerWallet || !escrowAddress || chainId == null)
        return setPhase({ s: 'error', message: '예치 정보가 아직 준비되지 않았습니다. 새로고침 후에도 반복되면 팀에 문의해 주세요.' })
      // 백엔드가 준 체인이 프론트가 지원하는 체인과 다르면 중단 — ensureSsafyChain 이 엉뚱한 체인에 예치하지 않게
      if (chainId !== SSAFY_CHAIN.id) return setPhase({ s: 'error', message: '지원하지 않는 네트워크 조건입니다. 관리자에게 문의해 주세요.' })
      const price = BigInt(priceWei) // Number() 금지 — 정밀도 손실

      const p = getProvider()
      if (!p) return setPhase({ s: 'error', message: 'MetaMask가 설치되어 있지 않습니다. 설치 후 다시 시도해 주세요.' })

      setPhase({ s: 'busy', label: '지갑 확인 중' })
      const account = await requestAccount(p)
      if (!account) return setPhase({ s: 'error', message: '지갑 계정을 확인하지 못했습니다. MetaMask에서 계정을 연결해 주세요.' })
      if (account.toLowerCase() !== clientWallet.toLowerCase())
        return setPhase({ s: 'error', message: `연결된 지갑(${shortAddress(account)})이 계정에 등록된 지갑(${shortAddress(clientWallet)})과 다릅니다. MetaMask에서 계정을 바꿔 주세요.` })
      await ensureSsafyChain(p)

      setPhase({ s: 'busy', label: '잔액 확인 중' })
      const balance = await getGscBalance(account)
      if (balance < price) return setPhase({ s: 'error', message: `GSC 잔액이 부족합니다. (보유 ${formatUnits(balance, 18, 2)} / 필요 ${formatUnits(price, 18, 2)} GSC)` })

      // 1/2 승인 — allowance 가 이미 충분하면 생략
      const allowance = await getGscAllowance(account, escrowAddress)
      if (allowance < price) {
        setPhase({ s: 'busy', label: '1/2 GSC 사용 승인 — MetaMask 서명 대기' })
        const approveHash = await approveGsc(p, account, escrowAddress, price)
        setPhase({ s: 'busy', label: '1/2 승인 처리 중 (블록 확인)' })
        const approveReceipt = await waitForReceipt(approveHash)
        if (approveReceipt.status !== '0x1') return setPhase({ s: 'error', message: '승인 트랜잭션이 실패했습니다. 다시 시도해 주세요.' })
      }

      // 2/2 예치 — 해시가 곧 서버 확인 열쇠. 채굴 확인은 사용자 안내용(서버 검증은 백엔드가 영수증으로)
      setPhase({ s: 'busy', label: '2/2 예치 — MetaMask 서명 대기' })
      const hash = await createAndFundJob(p, account, escrowAddress, externalRef, price, providerWallet)
      sentHash = hash
      setTxHash(hash)
      setPhase({ s: 'busy', label: '2/2 예치 처리 중 (블록 확인)' })
      const receipt = await waitForReceipt(hash)
      if (receipt.status !== '0x1') return setPhase({ s: 'error', message: '예치 트랜잭션이 실패했습니다. 승인 금액이 모자라거나 이미 예치된 작업일 수 있습니다.' })
      await report(hash)
    } catch (e) {
      if (isRejected(e)) setPhase({ s: 'error', message: '서명을 거절했습니다. 다시 시도할 수 있습니다.' })
      else if (e instanceof ReceiptTimeoutError && sentHash) setPhase({ s: 'pendingConfirm', txHash: sentHash, message: '블록 확인이 지연되고 있습니다. 트랜잭션을 다시 보내지 말고 잠시 후 서버 확인만 다시 눌러 주세요.' })
      else if (e instanceof ReceiptTimeoutError) setPhase({ s: 'error', message: '블록 확인이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.' })
      else setPhase({ s: 'error', message: '예치 처리 중 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.' })
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-primary/45 bg-primary/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-caption font-semibold text-dark">예치하고 학습 시작하기</span>
          <span className="text-caption text-text-muted">
            {job.estimatedCost.toLocaleString('ko-KR')} GSC를 에스크로 금고에 맡기면 워커 배정이 시작됩니다. MetaMask 서명이 두 번(승인·예치) 필요합니다.
          </span>
        </div>
        {phase.s !== 'pendingConfirm' && phase.s !== 'rejected' && (
          <Button disabled={busy} onClick={fund}>
            {busy ? phase.label + '…' : phase.s === 'error' ? '다시 예치 시도' : `${job.estimatedCost.toLocaleString('ko-KR')} GSC 예치`}
          </Button>
        )}
      </div>

      {(phase.s === 'error' || phase.s === 'rejected') && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-caption text-danger">{phase.message}</p>
      )}
      {phase.s === 'pendingConfirm' && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-warning-tint px-4 py-3">
          <p className="text-caption text-warning">{phase.message}</p>
          <Button variant="secondary" size="sm" onClick={() => report(phase.txHash)}>서버 확인 재시도</Button>
        </div>
      )}
      {txHash && (
        <button
          type="button"
          title={txHash}
          onClick={() => navigator.clipboard?.writeText(txHash).catch(() => {})}
          className="self-start text-caption text-text-muted tabular-nums hover:text-dark hover:underline"
        >
          트랜잭션 {txHash.slice(0, 10)}… 복사
        </button>
      )}
    </div>
  )
}
