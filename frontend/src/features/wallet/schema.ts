import * as z from 'zod/mini'

// 경로 상수는 여기(React 의존 없음) — mocks/handlers 가 api.ts 를 import 하면 authFetch → auth/store(useSyncExternalStore) 가 서버(instrumentation MSW) 모듈 그래프에 끌려와 RSC 빌드 오류
export const WALLET_PATH = '/users/me/wallet'

// 서버(WalletAddressNormalizer)는 소문자로 저장하지만 응답 검증은 대소문자 무관 40자 hex 로 넉넉히
export const addressSchema = z.string().check(z.regex(/^0x[0-9a-fA-F]{40}$/, '지갑 주소 형식이 올바르지 않습니다.'))

/** GET /users/me/wallet — 미연결이면 null */
export const walletSchema = z.object({ walletAddress: z.nullable(addressSchema) })

/** POST /users/me/wallet/challenge — message 는 서버가 만든 SIWE 원문, 그대로 서명한다 */
export const challengeSchema = z.object({
  challengeId: z.string().check(z.minLength(1)),
  message: z.string().check(z.minLength(1)),
  expiresAt: z.string(),
})
