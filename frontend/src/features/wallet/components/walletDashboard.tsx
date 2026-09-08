'use client'

import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { unlinkWallet } from '../api'
import { walletMessageFor } from '../errors'
import { GSC_TOKEN, SSAFY_CHAIN, formatUnits, getGscBalance, getNativeBalance, shortAddress } from '../ethereum'

// 지갑 있음 화면(wallet.md §5): 서버에 연결된 주소 기준. MetaMask 의 현재 계정/체인은 보지 않는다
type Balances = { gsc: bigint; native: bigint }

export function WalletDashboard({ address, onUnlinked }: { address: string; onUnlinked: () => void }) {
  const [balances, setBalances] = useState<Balances | null | 'error'>(null)
  const [copied, setCopied] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 응답이 온 뒤에만 setState(effect 안 동기 setState 금지). 주소가 바뀌면 다시 조회
  const loadBalances = useCallback(
    () =>
      Promise.all([getGscBalance(address), getNativeBalance(address)])
        .then(([gsc, native]) => setBalances({ gsc, native }))
        .catch(() => setBalances('error')),
    [address],
  )

  useEffect(() => {
    void loadBalances()
  }, [loadBalances])

  const retryBalances = () => {
    setBalances(null)
    void loadBalances()
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(address)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* 클립보드 불가 환경 — title 로 전체 주소는 보인다 */
    }
  }

  const unlink = async () => {
    if (!confirm('지갑 연결을 해제할까요? 다시 연결하면 같은 지갑을 쓸 수 있습니다.')) return
    setPending(true)
    setError(null)
    try {
      await unlinkWallet()
      onUnlinked()
    } catch (e) {
      setError(walletMessageFor(e))
      setPending(false)
    }
  }

  return (
    <section className="card animate-enter flex w-[520px] max-w-full flex-col gap-6 p-10">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-[13px] font-medium text-text-muted">연결된 지갑</span>
          <button type="button" onClick={copy} title={address} className="group flex items-center gap-2 text-left">
            <span className="text-h2 font-bold tabular-nums tracking-heading text-dark">{shortAddress(address)}</span>
            <span className="rounded-xs bg-neutral-tint px-1.5 py-0.5 text-[11px] text-text-muted group-hover:text-dark">{copied ? '복사됨' : '복사'}</span>
          </button>
        </div>
        <span className="rounded-pill bg-success-tint px-3 py-1 text-[12px] font-semibold text-success">
          {SSAFY_CHAIN.name} · {SSAFY_CHAIN.id}
        </span>
      </div>

      <dl className="flex flex-col divide-y divide-hairline rounded-lg border border-hairline bg-surface-1">
        <BalanceRow label={GSC_TOKEN.symbol} value={balances} pick={(b) => b.gsc} />
        <BalanceRow label={SSAFY_CHAIN.nativeCurrency.symbol} value={balances} pick={(b) => b.native} />
      </dl>
      {balances === 'error' && (
        <button type="button" onClick={retryBalances} className="self-start text-[13px] text-primary hover:underline">
          잔액을 불러오지 못했습니다. 다시 시도 →
        </button>
      )}

      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-[14px] text-danger">
          {error}
        </p>
      )}

      <Button variant="secondary" size="lg" className="w-full" disabled={pending} onClick={unlink}>
        {pending ? '해제 중…' : '지갑 연결 해제'}
      </Button>
    </section>
  )
}

function BalanceRow({ label, value, pick }: { label: string; value: Balances | null | 'error'; pick: (b: Balances) => bigint }) {
  return (
    <div className="flex items-center justify-between px-5 py-4">
      <dt className="text-[14px] font-medium text-text-muted">{label}</dt>
      <dd className="text-[18px] font-semibold tabular-nums text-dark">
        {value === null ? (
          <span aria-label="불러오는 중" className="inline-block h-5 w-24 animate-pulse rounded-xs bg-neutral-tint" />
        ) : value === 'error' ? (
          '—'
        ) : (
          formatUnits(pick(value))
        )}
      </dd>
    </div>
  )
}
