import type * as z from 'zod/mini'
import type { challengeSchema, walletSchema } from './schema'

export type Wallet = z.infer<typeof walletSchema>
export type Challenge = z.infer<typeof challengeSchema>

/** EIP-1193 프로바이더(MetaMask). 쓰는 메서드만 */
export type Eip1193Provider = {
  isMetaMask?: boolean
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>
}
