import { ApiError } from '@/lib/fetcher'

// 서버 에러 코드 → 화면 문구 (wallet.md §6). 토큰 401 은 authFetch 가 처리하므로 여기 없음
const SERVER: Record<string, string> = {
  WALLET_ALREADY_LINKED: '이미 다른 계정에 연결된 지갑입니다. MetaMask 에서 다른 계정을 선택해 주세요.',
  CHALLENGE_INVALID: '서명 요청이 만료되었습니다. 다시 시도해 주세요.',
  INVALID_WALLET_SIGNATURE: '서명 검증에 실패했습니다. 다시 시도해 주세요.',
  CHAIN_NOT_SUPPORTED: '지원하지 않는 네트워크입니다. SSAFY 네트워크로 전환해 주세요.',
  INVALID_WALLET_ADDRESS: '지갑 주소 형식이 올바르지 않습니다.',
}

// MetaMask(EIP-1193/1474) 오류 코드
const METAMASK: Record<number, string> = {
  4001: '지갑에서 요청을 취소했습니다.',
  [-32002]: 'MetaMask 창에서 대기 중인 요청을 먼저 처리해 주세요.',
}

const FALLBACK = '잠시 후 다시 시도해 주세요.'

export function walletMessageFor(err: unknown): string {
  if (err instanceof ApiError) return SERVER[err.code] ?? FALLBACK
  const code = (err as { code?: unknown } | null)?.code
  if (typeof code === 'number') return METAMASK[code] ?? FALLBACK
  return FALLBACK
}
