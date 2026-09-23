'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { issueChallenge, linkWallet } from '../api'
import { walletMessageFor } from '../errors'
import { SSAFY_CHAIN, ensureSsafyChain, getProvider, personalSign, requestAccount } from '../ethereum'

// 지갑 없음 화면. "지갑 생성" = MetaMask 연결(wallet.md §0). MetaMask 가 없으면 설치 안내가 곧 생성 경로
// 흐름(§4): 계정 요청 → SSAFY 체인 확인 → challenge → personal_sign → link. 실패 시 항상 처음부터(challenge 1회용)
type Step = 'connect' | 'chain' | 'challenge' | 'sign'
const STEP_LABEL: Record<Step, string> = {
  connect: '지갑 연결 중…',
  chain: '네트워크 확인 중…',
  challenge: '서명 요청 준비 중…',
  sign: 'MetaMask 에서 서명해 주세요…',
}

export function WalletConnectCard({ onLinked }: { onLinked: (address: string) => void }) {
  const [step, setStep] = useState<Step | null>(null)
  const [error, setError] = useState<string | null>(null)
  const provider = getProvider()

  const connect = async () => {
    if (!provider) return
    setError(null)
    try {
      setStep('connect')
      const address = await requestAccount(provider)
      setStep('chain')
      await ensureSsafyChain(provider)
      setStep('challenge')
      const challenge = await issueChallenge(address, SSAFY_CHAIN.id)
      setStep('sign')
      const signature = await personalSign(provider, challenge.message, address)
      onLinked(await linkWallet(challenge.challengeId, signature))
    } catch (e) {
      setError(walletMessageFor(e))
    } finally {
      setStep(null)
    }
  }

  return (
    <section className="card animate-enter flex w-[520px] max-w-full flex-col gap-6 p-10">
      <div className="flex flex-col gap-2">
        <h2 className="text-h2 font-bold tracking-heading text-dark">지갑 연결</h2>
        <p className="text-label-sm text-text-muted">
          MetaMask 지갑을 계정에 연결하면 GPU 작업 예치와 정산에 쓸 수 있습니다. 서명만 하며, 가스비는 들지 않습니다.
        </p>
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-danger-tint px-4 py-3 text-caption text-danger">
          {error}
        </p>
      )}

      {provider ? (
        <Button size="lg" className="w-full" disabled={step !== null} onClick={connect}>
          {step && <span aria-hidden className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
          {step ? STEP_LABEL[step] : 'MetaMask 지갑 연결'}
        </Button>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="rounded-md bg-neutral-tint px-4 py-3 text-caption text-dark">
            MetaMask 가 설치되어 있지 않습니다. 설치 후 MetaMask 안에서 지갑을 만들고 이 페이지로 돌아와 연결해 주세요.
          </p>
          <a href="https://metamask.io/download" target="_blank" rel="noreferrer" className="btn btn-lg btn-primary w-full">
            MetaMask 설치하기
          </a>
        </div>
      )}

      <p className="text-hint text-text-muted">
        네트워크: {SSAFY_CHAIN.name} (Chain ID {SSAFY_CHAIN.id}). 다른 네트워크에 있으면 연결 중 자동으로 전환을 요청합니다. 모바일은 MetaMask 앱
        브라우저에서 이용해 주세요.
      </p>
    </section>
  )
}
