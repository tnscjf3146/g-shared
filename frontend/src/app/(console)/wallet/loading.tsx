import { WalletSkeleton } from '@/features/wallet/components/walletSkeleton'

export default function Loading() {
  return (
    <div className="flex w-full flex-col items-center gap-8 pt-6">
      <h1 className="text-h1 font-bold tracking-heading text-dark">지갑</h1>
      <WalletSkeleton />
    </div>
  )
}
