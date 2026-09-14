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

/** MetaMask 자산 목록에 GSC 등록 제안 — 정책상 사용자 승인 팝업이 뜬다(강제 등록 불가). 거절해도 앱 동작엔 영향 없음 */
export async function watchGsc(p: Eip1193Provider): Promise<void> {
  await p.request({
    method: 'wallet_watchAsset',
    params: { type: 'ERC20', options: { address: GSC_TOKEN.address, symbol: GSC_TOKEN.symbol, decimals: GSC_TOKEN.decimals } },
  })
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

/* ------------------------------------------------------------------
 * 에스크로 예치(0914 연동 규격) — 라이브러리 없이 손 인코딩.
 * 셀렉터는 keccak256(시그니처) 앞 4바이트 — js-sha3 로 1회 계산해 상수로 박음(approve·allowance·balanceOf 가 알려진 값과 일치함을 확인)
 * ------------------------------------------------------------------ */
const SEL = {
  approve: '0x095ea7b3', // approve(address,uint256)
  allowance: '0xdd62ed3e', // allowance(address,address)
  createAndFundJob: '0xd903f3f5', // createAndFundJob(bytes32,uint256,address)
  cancelJob: '0x1dffa3dc', // cancelJob(uint256)
}
/** JobFunded(uint256 indexed jobId, bytes32 indexed externalRef, address indexed provider, …) 의 topic0 */
export const JOB_FUNDED_TOPIC = '0x91c5a5aa2f42d46f6921ece691c30445e39a95bf645ef9b8ab8e89ec661db526'

/** 주소·정수·bytes32 → 32바이트 hex(0x 없이). 인자가 전부 정적 타입이라 이어붙이면 끝 */
const pad = (hexOrBig: string | bigint) => (typeof hexOrBig === 'bigint' ? hexOrBig.toString(16) : hexOrBig.replace(/^0x/, '').toLowerCase()).padStart(64, '0')

/** TOKEN 정수(예: 60) → wei(60 × 10^18). 백엔드 estimatedCost 가 TOKEN 단위라 이 변환을 빠뜨리면 10^18배 틀린다 */
export function toWei(token: number | string): bigint {
  return BigInt(token) * BigInt(10) ** BigInt(GSC_TOKEN.decimals)
}

/** ERC-20 allowance(owner, spender) — 공개 RPC 읽기 */
export async function getGscAllowance(owner: string, spender: string): Promise<bigint> {
  const data = SEL.allowance + pad(owner) + pad(spender)
  return BigInt(await rpc('eth_call', [{ to: GSC_TOKEN.address, data }, 'latest']))
}

/** MetaMask 로 트랜잭션 전송 → 해시. 서명 거절은 code 4001 로 throw */
async function sendTx(p: Eip1193Provider, from: string, to: string, data: string): Promise<string> {
  return (await p.request({ method: 'eth_sendTransaction', params: [{ from, to, data }] })) as string
}

/** 1/2 GCoin.approve(escrow, priceWei) — 무한 승인 금지, 정확히 price 만 */
export function approveGsc(p: Eip1193Provider, from: string, escrowAddress: string, priceWei: bigint): Promise<string> {
  return sendTx(p, from, GSC_TOKEN.address, SEL.approve + pad(escrowAddress) + pad(priceWei))
}

/** 2/2 Escrow.createAndFundJob(externalRef, priceWei, provider) — 인자는 백엔드가 준 값 그대로, 프론트가 만들지 않는다 */
export function createAndFundJob(p: Eip1193Provider, from: string, escrowAddress: string, externalRef: string, priceWei: bigint, providerWallet: string): Promise<string> {
  return sendTx(p, from, escrowAddress, SEL.createAndFundJob + pad(externalRef) + pad(priceWei) + pad(providerWallet))
}

/** FUNDED 취소 — Client 지갑으로 직접 cancelJob(escrowJobId) */
export function cancelEscrowJob(p: Eip1193Provider, from: string, escrowAddress: string, escrowJobId: bigint): Promise<string> {
  return sendTx(p, from, escrowAddress, SEL.cancelJob + pad(escrowJobId))
}

export type TxReceipt = { status: string; logs: { address: string; topics: string[] }[] }
/** 영수증 폴링(2초 간격, 기본 90초) — 없으면 null 유지, 채굴되면 성공 여부까지 확인해서 반환 */
export async function waitForReceipt(hash: string, timeoutMs = 90_000): Promise<TxReceipt> {
  const until = Date.now() + timeoutMs
  for (;;) {
    const res = await fetch(SSAFY_CHAIN.rpcUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_getTransactionReceipt', params: [hash] }),
    })
    const body = (await res.json().catch(() => null)) as { result?: TxReceipt | null } | null
    if (body?.result) return body.result
    if (Date.now() > until) throw new Error('receipt timeout')
    await new Promise((r) => setTimeout(r, 2_000))
  }
}

/** 영수증에서 이 예치의 JobFunded 이벤트를 찾아 온체인 jobId(topics[1])를 십진 문자열로.
 *  jobId·externalRef 가 indexed 라 topic 비교만으로 찾는다 — nextJobId 선읽기(경쟁 시 틀림) 금지 */
export function extractEscrowJobId(receipt: TxReceipt, escrowAddress: string, externalRef: string): string | null {
  const log = receipt.logs.find(
    (l) => l.address.toLowerCase() === escrowAddress.toLowerCase() && l.topics[0] === JOB_FUNDED_TOPIC && l.topics[2]?.toLowerCase() === externalRef.toLowerCase(),
  )
  return log ? BigInt(log.topics[1]).toString() : null
}
