import { delay, http, HttpResponse } from 'msw'
import { API_BASE_URL } from '@/lib/fetcher'
import { WALLET_PATH } from '../schema'
import { MOCK_CHAIN_ID, MOCK_CHALLENGE_ID, MOCK_TAKEN_ADDRESS, MOCK_WALLET_INITIAL } from './data'

const URL = `${API_BASE_URL}${WALLET_PATH}`
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/

// 연결 상태는 모듈 변수 — 새로고침하면 MOCK_WALLET_INITIAL 로 돌아간다(두 화면 다 보기 편함)
let linked: string | null = MOCK_WALLET_INITIAL
let pendingAddress: string | null = null // challenge 발급 시 저장, link 에서 소비(서버의 Redis 역할)

// 서명 검증은 하지 않는다 — MetaMask 는 MSW 로 흉내낼 수 없어 진짜 지갑으로 서명하고, 목업은 통과만 시킨다
export const walletHandlers = [
  http.get(URL, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    return HttpResponse.json({ walletAddress: linked })
  }),

  http.post(`${URL}/challenge`, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const body = (await request.json().catch(() => null)) as { walletAddress?: string; chainId?: number } | null
    const address = body?.walletAddress?.trim().toLowerCase() ?? ''
    if (!ADDRESS_RE.test(address)) return error(400, 'INVALID_WALLET_ADDRESS', '지갑 주소 형식이 올바르지 않습니다.')
    if (body?.chainId !== MOCK_CHAIN_ID) return error(400, 'CHAIN_NOT_SUPPORTED', '지원하지 않는 네트워크입니다.')
    if (address === MOCK_TAKEN_ADDRESS) return error(409, 'WALLET_ALREADY_LINKED', '이미 다른 계정에 연결된 지갑입니다.')
    pendingAddress = address
    const now = Date.now()
    return HttpResponse.json({
      challengeId: MOCK_CHALLENGE_ID,
      message: `localhost:3000 wants you to sign in with your Ethereum account:\n${address}\n\nLink this wallet to your gshared account.\n\nURI: http://localhost:3000\nVersion: 1\nChain ID: ${MOCK_CHAIN_ID}\nNonce: mocknonce\nIssued At: ${new Date(now).toISOString()}\nExpiration Time: ${new Date(now + 5 * 60_000).toISOString()}\nRequest ID: ${MOCK_CHALLENGE_ID}`,
      expiresAt: new Date(now + 5 * 60_000).toISOString(),
    })
  }),

  http.post(URL, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    const body = (await request.json().catch(() => null)) as { challengeId?: string; signature?: string } | null
    if (body?.challengeId !== MOCK_CHALLENGE_ID || !pendingAddress) return error(410, 'CHALLENGE_INVALID', '만료되었거나 이미 사용된 요청입니다. 다시 시도해주세요.')
    if (!body.signature) return error(401, 'INVALID_WALLET_SIGNATURE', '지갑 서명이 올바르지 않습니다.')
    linked = pendingAddress
    pendingAddress = null // 1회용
    return HttpResponse.json({ walletAddress: linked })
  }),

  http.delete(URL, async ({ request }) => {
    await delay()
    if (!request.headers.get('Authorization')) return error(401, 'UNAUTHORIZED', '인증이 필요합니다.')
    linked = null
    return new HttpResponse(null, { status: 204 })
  }),
]
