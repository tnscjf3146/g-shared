'use client'

import { useCallback, useEffect, useState } from 'react'
import { getWallet } from '../api'
import { WalletConnectCard } from './walletConnectCard'
import { WalletDashboard } from './walletDashboard'
import { WalletSkeleton } from './walletSkeleton'

// /wallet 본문(wallet.md §2): GET 한 번으로 분기 — null 이면 연결 카드, 주소면 대시보드. 연결/해제 뒤엔 라우트 이동 없이 상태만 바꾼다
// 토큰 없음·만료는 authFetch 가 reissue 후 /login?next= 으로 보내므로 여기선 다루지 않는다
type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; address: string | null }

export function WalletPage() {
  const [state, setState] = useState<State>({ status: 'loading' })

  // 응답이 온 뒤에만 setState(effect 안 동기 setState 금지 — react-hooks/set-state-in-effect). 초기 상태가 이미 loading
  const load = useCallback(
    () =>
      getWallet()
        .then(({ walletAddress }) => setState({ status: 'ready', address: walletAddress }))
        .catch(() => setState({ status: 'error' })),
    [],
  )

  useEffect(() => {
    void load()
  }, [load])

  if (state.status === 'loading') return <WalletSkeleton />
  if (state.status === 'error') {
    return (
      <section className="card flex w-[520px] max-w-full flex-col items-start gap-3 p-10">
        <p className="text-[15px] text-dark">지갑 정보를 불러오지 못했습니다.</p>
        <button
          type="button"
          onClick={() => {
            setState({ status: 'loading' })
            void load()
          }}
          className="text-[14px] text-primary hover:underline"
        >
          다시 시도 →
        </button>
      </section>
    )
  }
  return state.address ? (
    <WalletDashboard address={state.address} onUnlinked={() => setState({ status: 'ready', address: null })} />
  ) : (
    <WalletConnectCard onLinked={(address) => setState({ status: 'ready', address })} />
  )
}
