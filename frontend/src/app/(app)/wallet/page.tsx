import type { Metadata } from 'next'
import { WalletPage } from '@/features/wallet/components/walletPage'

export const metadata: Metadata = { title: '지갑 — G-shared' }

export default function Page() {
  return (
    <div className="flex w-full max-w-content flex-col items-center gap-8">
      <h1 className="text-h1 font-bold tracking-heading text-dark">지갑</h1>
      <WalletPage />
    </div>
  )
}
