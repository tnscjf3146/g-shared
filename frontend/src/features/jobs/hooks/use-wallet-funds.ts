import { useCallback, useEffect, useState } from 'react'
import { getWallet } from '@/features/wallet/api'
import { getGscBalance } from '@/features/wallet/ethereum'

// 5단계 지갑 확인: 지갑 주소(백엔드) → GSC 잔액(SSAFY 공개 RPC). enabled 가 true 가 되는 순간 한 번 확인하고, recheck 로 다시 확인한다
export type Funds = { status: 'loading' } | { status: 'nowallet' } | { status: 'error' } | { status: 'ready'; address: string; balance: bigint }
export const ONE_GSC = BigInt(10) ** BigInt(18) // GSC decimals 18

export function useWalletFunds(enabled: boolean) {
  const [funds, setFunds] = useState<Funds>({ status: 'loading' })
  const check = useCallback(
    () =>
      getWallet()
        .then(({ walletAddress }) => (walletAddress ? getGscBalance(walletAddress).then((balance) => setFunds({ status: 'ready', address: walletAddress, balance })) : setFunds({ status: 'nowallet' })))
        .catch(() => setFunds({ status: 'error' })),
    [],
  )
  useEffect(() => {
    if (enabled) void check()
  }, [enabled, check])
  const recheck = () => {
    setFunds({ status: 'loading' })
    void check()
  }
  return { funds, recheck }
}

/** 예상 비용(GSC) 만큼 잔액이 있는지 */
export function hasEnough(funds: Funds, cost: number): boolean {
  return funds.status === 'ready' && funds.balance >= BigInt(Math.ceil(cost)) * ONE_GSC
}
