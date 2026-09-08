import { authFetch } from '@/features/auth/api'
import { WALLET_PATH, challengeSchema, walletSchema } from './schema'
import type { Challenge, Wallet } from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** GET → { walletAddress | null } */
export async function getWallet(): Promise<Wallet> {
  return walletSchema.parse(await authFetch<unknown>(WALLET_PATH))
}

/** POST /challenge → 서명할 SIWE 원문. 이미 다른 계정 지갑이면 409 WALLET_ALREADY_LINKED */
export async function issueChallenge(walletAddress: string, chainId: number): Promise<Challenge> {
  const res = await authFetch<unknown>(`${WALLET_PATH}/challenge`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ walletAddress, chainId }),
  })
  return challengeSchema.parse(res)
}

/** POST → 서명 검증 후 연결. challenge 는 1회용(실패 시 재발급부터) */
export async function linkWallet(challengeId: string, signature: string): Promise<string> {
  const res = await authFetch<unknown>(WALLET_PATH, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ challengeId, signature }) })
  const { walletAddress } = walletSchema.parse(res)
  if (!walletAddress) throw new Error('empty wallet address')
  return walletAddress
}

/** DELETE → 204 */
export function unlinkWallet(): Promise<void> {
  return authFetch<void>(WALLET_PATH, { method: 'DELETE' })
}
