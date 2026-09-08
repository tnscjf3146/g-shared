import type { Eip1193Provider } from './types'

// 체인·토큰 상수 — blockchain/deployments/ssafy-31221 기준. 서버 wallet-auth.chain-id(31221) 와 같아야 challenge 가 통과한다
export const SSAFY_CHAIN = {
  id: 31221,
  hex: '0x79f5',
  name: 'SSAFY',
  rpcUrl: 'https://rpc.ssafy-blockchain.com',
  // ponytail: SSAFY 체인 네이티브 통화 심볼 미확인 — 지갑에 "네트워크 추가" 될 때만 보이는 표시용. 확인되면 교체
  nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
}
export const GSC_TOKEN = { address: '0xaCe3c977A539C16668528619F9417589f12C7B22', symbol: 'GSC', decimals: 18 }

// 잔액 조회는 MetaMask 상태(잠김·미설치·다른 계정)와 무관해야 하므로 공개 RPC 로 직접 JSON-RPC.
// SSAFY 노드는 요청 Origin 을 그대로 허용(access-control-allow-origin 반영)해 브라우저에서 바로 호출 가능 — 프록시 불필요(2026-09-08 확인)

/** 설치된 MetaMask(EIP-1193). 없으면 null → 설치 안내 */
export function getProvider(): Eip1193Provider | null {
  if (typeof window === 'undefined') return null
  return (window as { ethereum?: Eip1193Provider }).ethereum ?? null
}

/** 계정 선택 팝업 → 첫 계정 주소. 사용자가 닫으면 code 4001 */
export async function requestAccount(p: Eip1193Provider): Promise<string> {
  const accounts = (await p.request({ method: 'eth_requestAccounts' })) as string[]
  if (!accounts?.[0]) throw new Error('no account')
  return accounts[0]
}

/** 현재 체인이 SSAFY 가 아니면 전환. 지갑에 없는 체인(4902)이면 추가 후 전환 */
export async function ensureSsafyChain(p: Eip1193Provider): Promise<void> {
  const current = (await p.request({ method: 'eth_chainId' })) as string
  if (parseInt(current, 16) === SSAFY_CHAIN.id) return
  try {
    await p.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: SSAFY_CHAIN.hex }] })
  } catch (e) {
    if ((e as { code?: number })?.code !== 4902) throw e
    await p.request({
      method: 'wallet_addEthereumChain',
      params: [{ chainId: SSAFY_CHAIN.hex, chainName: SSAFY_CHAIN.name, rpcUrls: [SSAFY_CHAIN.rpcUrl], nativeCurrency: SSAFY_CHAIN.nativeCurrency }],
    })
  }
}

/** personal_sign — 서버가 준 문장을 재조립 없이 그대로. 순서는 [message, address] */
export async function personalSign(p: Eip1193Provider, message: string, address: string): Promise<string> {
  return (await p.request({ method: 'personal_sign', params: [message, address] })) as string
}

async function rpc(method: string, params: unknown[]): Promise<string> {
  const res = await fetch(SSAFY_CHAIN.rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  })
  if (!res.ok) throw new Error(`rpc ${res.status}`)
  const body = (await res.json()) as { result?: string; error?: { message: string } }
  if (body.error || typeof body.result !== 'string') throw new Error(body.error?.message ?? 'rpc error')
  return body.result
}

/** 네이티브 잔액(wei) */
export async function getNativeBalance(address: string): Promise<bigint> {
  return BigInt(await rpc('eth_getBalance', [address, 'latest']))
}

/** ERC-20 balanceOf(address) — 선택자 0x70a08231 + 32바이트 패딩 주소. 라이브러리 없이 손으로 인코딩 */
export async function getGscBalance(address: string): Promise<bigint> {
  const data = '0x70a08231' + address.slice(2).toLowerCase().padStart(64, '0')
  return BigInt(await rpc('eth_call', [{ to: GSC_TOKEN.address, data }, 'latest']))
}

/** 0x1234…abcd */
export function shortAddress(address: string): string {
  return `${address.slice(0, 6)}…${address.slice(-4)}`
}

/** 최소 단위 → 소수 fraction 자리 문자열. BigInt 정수 연산(부동소수 오차 없음) */
export function formatUnits(value: bigint, decimals = 18, fraction = 4): string {
  const base = BigInt(10) ** BigInt(decimals) // tsconfig target ES2017 → 10n 리터럴 불가, 런타임 BigInt 는 사용 가능
  const whole = (value / base).toLocaleString('ko-KR')
  const frac = (value % base).toString().padStart(decimals, '0').slice(0, fraction)
  return fraction ? `${whole}.${frac}` : whole
}
