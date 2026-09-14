// 고정 fixture. 주소는 Hardhat 공개 테스트 계정 #0 (실제 자산 없음)
export const MOCK_WALLET_ADDRESS = '0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266'
/** 다른 계정에 이미 묶인 지갑 — challenge 단계 409 재현용 */
export const MOCK_TAKEN_ADDRESS = '0x70997970c51812dc3a010c7d01b50e0d17dc79c8'
/** 새로고침 직후 화면: null = 연결 카드, 주소 = 대시보드.
 *  MetaMask 없이 예치 패널 UI 를 보려면 임시로 MOCK_WALLET_ADDRESS 로 두면 "연결됨" 으로 시작한다(실제 서명은 여전히 불가) */
export const MOCK_WALLET_INITIAL: string | null = null
export const MOCK_CHALLENGE_ID = 'mock-challenge'
export const MOCK_CHAIN_ID = 31221
