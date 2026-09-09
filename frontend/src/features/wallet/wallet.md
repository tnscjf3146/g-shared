# 지갑 탭 · 지갑 대시보드 · 지갑 연결 구현 계획 (프론트)

브랜치: `fe-feature/wallet-create` (develop 최신에서 분기). 백엔드 지갑 API 는 `back/.../user/controller/UserWalletController.java` 의 **연결(link)** 흐름(develop `11ca4e7`) + 이 브랜치에서 추가한 **조회(GET)**(§1-2, 2026-09-08 사용자 결정으로 프론트 브랜치에 포함). 네비 `지갑` 탭 → `/wallet` 한 화면(지갑 있음 = 대시보드 / 없음 = 연결 카드). **2026-09-08 구현 완료 — 아래는 구현 기준 설명.** 검증 방법은 §9.

## 0. 범위와 용어

- **"지갑 생성" = MetaMask 지갑을 내 계정에 연결하는 것.** 백엔드는 개인키를 만들지도 보관하지도 않는다(`blockchain-skeleton.md` §8: 프론트·백엔드에 Private Key 금지, 자금 서명은 사용자 MetaMask). 따라서 커스터디얼 "지갑 만들어 주기"는 만들지 않는다. MetaMask 가 없는 사용자에게는 **설치 → MetaMask 안에서 지갑 생성 → 돌아와서 연결** 안내를 보여 준다.
- **한다**: 네비 `지갑` 탭, `/wallet` 보호 라우트, 연결 상태 조회, 연결 카드(MetaMask 연결 + SIWE 서명), 대시보드(주소·네트워크·GSC/ETH 잔액·연결 해제), MSW 핸들러, 백엔드 조회 API 요청.
- **안 한다**: 지갑으로 로그인(비밀번호 없는 SIWE 로그인 — 백엔드 엔드포인트 없음), 송금/예치/정산(Escrow 미구현), 거래 내역, 여러 지갑, WalletConnect 등 MetaMask 외 지갑, 모바일 딥링크.
- **전제**: 라이브러리 추가 없음. `window.ethereum`(EIP-1193) 을 직접 호출하고, ERC-20 `balanceOf` 는 함수 선택자 + 주소 패딩으로 손으로 인코딩한다(ethers/viem/wagmi 미도입 — 필요한 호출이 5개라 수백 KB 를 들일 이유가 없다). 응답은 zod 로 경계 검증(AGENTS.md).

## 1. 백엔드 API

### 1-1. 있는 것 (`/api/users/me/wallet`, 모두 `Authorization: Bearer` 필수)

| 메서드/경로 | 요청 | 성공 | 실패 코드(`{ code, message }`) |
| --- | --- | --- | --- |
| `POST /api/users/me/wallet/challenge` | `{ walletAddress, chainId }` | `200 { challengeId, message, expiresAt }` | `INVALID_WALLET_ADDRESS` 400, `CHAIN_NOT_SUPPORTED` 400, `WALLET_ALREADY_LINKED` 409 |
| `POST /api/users/me/wallet` | `{ challengeId, signature }` | `200 { walletAddress }` | `CHALLENGE_INVALID` 410, `INVALID_WALLET_SIGNATURE` 401, `WALLET_ALREADY_LINKED` 409, `USER_NOT_FOUND` 404 |
| `DELETE /api/users/me/wallet` | 없음 | `204` | `USER_NOT_FOUND` 404 |

- `message` 는 SIWE(EIP-4361) 원문. **재조립 금지, 받은 그대로 `personal_sign`** (`WalletChallengeResponse` 주석). challenge 는 Redis TTL **5분**, 1회용(꺼내면 삭제 → 서명 실패해도 재발급부터).
- `chainId` 는 서버 설정 `wallet-auth.chain-id` 와 같아야 한다. 기본 **31221(SSAFY)**, 로컬 Hardhat 은 31337 로 덮어씀. 주소는 서버가 소문자 정규화 + 형식 검사.
- 이미 연결된 지갑이 있으면 새 지갑으로 **교체**된다(`UserWalletService.link` 주석). 다른 계정에 묶인 주소면 409.

### 1-2. 이 브랜치에서 추가 — `GET /api/users/me/wallet` ✅

develop 에는 "지갑이 있는지" 알 방법이 없었다(`/users/me` 없음, 토큰 payload 는 `sub`·`role` 만). 분기(대시보드 vs 연결 카드)가 이 조회 하나에 달려 있어 백엔드 4파일(컨트롤러·서비스·테스트 2)을 이 브랜치에 함께 넣었다. **MR 리뷰 때 백엔드 담당에게 알릴 것.**

| 메서드/경로 | 성공 | 실패 |
| --- | --- | --- |
| `GET /api/users/me/wallet` | `200 { walletAddress: string \| null }` (미연결 = `null`) | `USER_NOT_FOUND` 404, 토큰 401 |

- `UserWalletController.get` → `UserWalletService.getWallet(userId)` → 기존 `WalletResponse` 재사용(43줄). 단위 테스트 `UserWalletServiceTest.getsWallet` 통과. 통합 테스트 `UserWalletIntegrationTest.getsWallet` 은 MySQL·Redis 필요 — CI 에서 확인.
- 닉네임 표시(auth.md §8)까지 풀려면 `GET /users/me` 가 따로 필요 — 이번 범위 밖.

## 2. 화면 진입 · 상태 분기

```
네비 [지갑] 클릭
  ├─ guest  → proxy.ts 가 /login?next=/wallet 로 (refresh 쿠키 없음)
  └─ authed → /wallet
        GET /users/me/wallet  (authFetch)
        ├─ loading            → 스켈레톤(loading.tsx)
        ├─ walletAddress=null → <WalletConnectCard/>  "지갑 연결"
        └─ walletAddress      → <WalletDashboard/>
```

- 네비 `지갑` 은 **로그인한 뒤에만 보인다**(2026-09-08 변경, 처음엔 항상 표시였음). guest 가 URL 로 직접 `/wallet` 에 오면 proxy 가 로그인 → `next` 로 돌려보낸다. `navAuth.tsx` 안에서 authed 일 때 로그아웃 버튼 **왼쪽**에 `<Link href="/wallet" className="nav-btn">지갑</Link>` 하나 추가(같은 `nav-btn`, 색은 회색 토큰만 — AGENTS.md 버튼 규칙). `siteNav.tsx` 의 `MENU` 는 건드리지 않는다(마케팅 링크).
- `/wallet` 은 `proxy.ts` 의 `PROTECTED` 와 `config.matcher` 두 곳에 추가(auth.md §4 규칙). 쿠키가 있어도 만료일 수 있으니 최종 판정은 `authFetch` 의 401 → reissue → 실패 시 `/login?next=/wallet&reason=expired` (이미 구현된 흐름).
- 연결 성공 → 같은 페이지에서 상태만 대시보드로 바꾼다(라우트 이동 없음). 해제 성공 → 연결 카드로.

## 3. 파일 구조 (구현) — AGENTS.md 골격

```
src/
  app/
    (app)/                              로그인 필요 화면 라우트 그룹 — L0 배경 + 랜딩과 같은 고정 유리 네비(SiteNav 재사용)      ✅
      layout.tsx                        서버 컴포넌트. body[data-nav-theme] 없음 = 밝은 배경용 기본 색, 위 72px 비움           ✅
      wallet/page.tsx                   h1 + <WalletPage/>('use client' 는 잎으로)                                              ✅
      wallet/loading.tsx                <WalletSkeleton/>                                                                        ✅
  proxy.ts                              PROTECTED += '/wallet', matcher += '/wallet/:path*'                                      ✅
  features/auth/components/navAuth.tsx  authed 일 때만 [지갑] 링크 + 로그아웃, guest 는 로그인만 — 같은 nav-btn                                        ✅
  features/auth/mocks/handlers.ts       목업 refresh 쿠키에서 HttpOnly 제거(§7 이유)                                              ✅
  features/wallet/
    wallet.md                           이 문서
    schema.ts                           WALLET_PATH 상수 · addressSchema · walletSchema{ walletAddress: nullable } · challengeSchema ✅
    types.ts                            Wallet · Challenge · Eip1193Provider(request 만)                                          ✅
    errors.ts                           서버 코드 + MetaMask 코드(4001/-32002) → 문구(§6) walletMessageFor                        ✅
    api.ts                              getWallet / issueChallenge / linkWallet / unlinkWallet — authFetch + zod parse             ✅
    ethereum.ts                         SSAFY_CHAIN·GSC_TOKEN 상수, getProvider/requestAccount/ensureSsafyChain/personalSign,
                                        공개 RPC 직접 호출 getNativeBalance/getGscBalance(balanceOf 손 인코딩), shortAddress/formatUnits ✅
    components/walletPage.tsx           'use client'. GET 1회 → loading | error(다시 시도) | ConnectCard | Dashboard              ✅
    components/walletConnectCard.tsx    'use client'. MetaMask 없음 안내 / 연결 버튼(단계 라벨) / 오류 인라인                     ✅
    components/walletDashboard.tsx      'use client'. 주소(복사) · 네트워크 배지 · GSC/ETH 잔액 · 연결 해제(confirm)              ✅
    components/walletSkeleton.tsx       카드 스켈레톤(loading.tsx 와 조회 중 공용)                                                ✅
    mocks/handlers.ts                   walletHandlers: GET / challenge / link / DELETE — 모듈 변수로 연결 상태                    ✅
    mocks/data.ts                       MOCK_WALLET_ADDRESS · MOCK_TAKEN_ADDRESS(409 재현) · MOCK_WALLET_INITIAL                   ✅
  mocks/handlers.ts                     [...jobHandlers, ...authHandlers, ...walletHandlers]                                     ✅
```

- 계획에 있던 `hooks/use-wallet.ts`·`use-balances.ts`·`format.ts`·`eip1193.d.ts` 는 만들지 않았다 — 화면 하나가 쓰는 상태라 컴포넌트 안 `useState`/`useCallback` 으로 충분, 포맷 함수는 `ethereum.ts` 에, 프로바이더 타입은 `types.ts` 에. 다른 화면이 지갑 상태를 쓰게 되면 그때 훅/스토어로 승격.
- **주의: `mocks/handlers.ts` 는 `api.ts` 를 import 하면 안 된다.** `api.ts → auth/api → auth/store(useSyncExternalStore)` 가 서버(instrumentation MSW) 모듈 그래프에 끌려와 RSC 빌드 오류. 경로 상수를 `schema.ts` 에 둔 이유.
- 네이밍: 컴포넌트 camelCase, 나머지 kebab-case, 배럴 없음. `'use client'` 는 `walletPage.tsx` 이하로만.

## 4. 연결 흐름 (`walletConnectCard.tsx` + `ethereum.ts`)

```
[지갑 연결] 클릭
 1. window.ethereum 없음            → 버튼 대신 "MetaMask 설치" 링크(metamask.io) + "설치 후 지갑을 만들고 이 페이지로 돌아오세요" 안내 (= 지갑 생성 경로)
 2. eth_requestAccounts              → address (사용자 거절 4001 → "지갑 연결을 취소했습니다.")
 3. eth_chainId ≠ 0x79f5(31221)       → wallet_switchEthereumChain, 4902 면 wallet_addEthereumChain(SSAFY rpc) 후 재시도. 거절 → 안내
 4. POST /challenge { address, 31221 } → { challengeId, message }
 5. personal_sign(message, address)   → signature (거절 4001 → 안내, challenge 는 버림)
 6. POST /wallet { challengeId, signature } → { walletAddress } → 상태 linked, 대시보드로 전환
```

- 단계 표시: 버튼 라벨을 `연결 중… → 네트워크 확인… → 서명 대기…` 로 바꾸고 비활성(인라인 스피너, AGENTS.md 버튼 규칙). 별도 스텝퍼 UI 는 만들지 않는다.
- `personal_sign` 은 `[message, address]` 순서, message 는 UTF-8 문자열 그대로(hex 인코딩 불필요 — MetaMask 가 처리). 서버가 `EthereumSignatureVerifier.recoverAddress` 로 복원한 주소를 challenge 의 소문자 주소와 비교하므로 프론트가 주소 대소문자를 손볼 필요 없음.
- 실패 후 재시도는 항상 1단계부터(challenge 1회용). 뮤테이션 자동 재시도 금지.
- `accountsChanged`/`chainChanged` 이벤트는 **연결 카드에서만** 듣지 않아도 된다 — 연결 시점에 계정·체인을 다시 읽기 때문. 대시보드는 "MetaMask 의 현재 계정"이 아니라 "서버에 연결된 주소"를 보여 주므로 이벤트 구독 없음.
- 이미 다른 계정에 묶인 주소면 challenge 단계에서 409 → "이미 다른 계정에 연결된 지갑입니다. MetaMask 에서 다른 계정을 선택해 주세요."

## 5. 대시보드 (`walletDashboard.tsx` + `use-balances.ts`)

카드 하나(`card`, L2). 위에서 아래로:
1. **주소**: `0x1234…abcd` + 복사 버튼(`navigator.clipboard.writeText`, 성공 시 1.5초 "복사됨"). 전체 주소는 `title` 로.
2. **네트워크**: `SSAFY (31221)` 배지 — 서버 chainId 고정값이므로 연결된 지갑은 항상 이 네트워크. MetaMask 현재 체인은 표시하지 않는다(사용자 혼동만 늘린다).
3. **잔액**: `GSC` · `ETH`(네이티브) 두 줄, `tabular-nums`, 소수 4자리. 로딩은 자리 스켈레톤, 실패는 `—` + 다시 시도 버튼.
4. **연결 해제** `secondary` 버튼 → `confirm()` 한 번(네이티브 다이얼로그, 모달 컴포넌트 없음) → `DELETE` → 연결 카드로.

잔액 조회는 **공개 RPC(`https://rpc.ssafy-blockchain.com`) 에 JSON-RPC POST 를 직접** 보낸다(`fetch`). MetaMask 잠김·미설치·다른 탭과 무관하게, 서버가 준 주소로 읽을 수 있어야 하기 때문.
- `eth_getBalance(address, 'latest')` → wei
- `eth_call({ to: GSC, data: '0x70a08231' + address.slice(2).padStart(64,'0') }, 'latest')` → uint256 hex
- 두 호출을 `Promise.all`, 결과는 `BigInt(hex)` → `formatUnits`(BigInt 정수 연산, 소수 4자리). SSAFY 노드는 요청 Origin 을 그대로 허용해 브라우저 직접 호출이 된다 — Next rewrite 프록시는 시험 후 제거(2026-09-08, 배포자 지갑 999,998 GSC 로 인코딩 확인).

## 6. 에러 메시지 표 (`errors.ts`)

| 출처 | 코드 | 화면 문구 |
| --- | --- | --- |
| 서버 | `WALLET_ALREADY_LINKED` 409 | 이미 다른 계정에 연결된 지갑입니다. MetaMask 에서 다른 계정을 선택해 주세요. |
| 서버 | `CHALLENGE_INVALID` 410 | 서명 요청이 만료되었습니다. 다시 시도해 주세요. |
| 서버 | `INVALID_WALLET_SIGNATURE` 401 | 서명 검증에 실패했습니다. 다시 시도해 주세요. |
| 서버 | `CHAIN_NOT_SUPPORTED` 400 | 지원하지 않는 네트워크입니다. SSAFY 네트워크로 전환해 주세요. |
| 서버 | `INVALID_WALLET_ADDRESS` 400 | 지갑 주소 형식이 올바르지 않습니다. |
| MetaMask | `4001` | 지갑에서 요청을 취소했습니다. |
| MetaMask | `4902` | (자동 처리: 네트워크 추가) |
| MetaMask | `-32002` | MetaMask 창에서 대기 중인 요청을 먼저 처리해 주세요. |
| 환경 | 프로바이더 없음 | MetaMask 가 설치되어 있지 않습니다. (설치 링크) |
| 그 외 / RPC / 네트워크 | | 잠시 후 다시 시도해 주세요. |

토큰 401 은 `authFetch` 가 처리하므로 여기서 다루지 않는다. 문구는 연결 카드 **버튼 위 인라인**(auth 폼과 동일 위치 규칙, 토스트 스토어 없음).

## 7. 목업(MSW) — `walletHandlers`

`NEXT_PUBLIC_API_MOCKING=enabled` 일 때만. 경로는 `${API_BASE_URL}/users/me/wallet...` 상수.

- 모듈 변수 `linked: string | null = null` 로 상태 유지(새로고침 시 초기화 — 두 화면 다 보려면 편함). 초기값을 연결 상태로 보려면 `data.ts` 의 `MOCK_WALLET_INITIAL` 만 바꾼다.
- `GET`: `Authorization` 없으면 401 `UNAUTHORIZED`, 있으면 `{ walletAddress: linked }`.
- `challenge`: 주소 형식 틀리면 400, `chainId !== 31221` 이면 400 `CHAIN_NOT_SUPPORTED`, 주소가 `MOCK_TAKEN_ADDRESS` 면 409, 아니면 `{ challengeId: 'mock-ch', message: <SIWE 형태 고정 문자열>, expiresAt: now+5m }`.
- `link`: `challengeId !== 'mock-ch'` → 410, signature 비어 있으면 401, 아니면 `linked = 소문자 주소` 후 200.
- `DELETE`: 204, `linked = null`.
- **MetaMask 는 MSW 로 못 가짜 만든다.** 목업 모드에서도 서명은 진짜 MetaMask 로 한다(서명 검증은 목업이 안 하므로 어떤 계정이든 통과). 잔액 RPC 는 실제 SSAFY RPC 로 나간다(`onUnhandledRequest: 'bypass'`).
- 연결 상태(`linked`)는 페이지 JS 의 모듈 변수 → **새로고침하면 `MOCK_WALLET_INITIAL` 로 돌아간다**(MSW 핸들러는 워커가 아니라 페이지 컨텍스트에서 실행). 실서버는 DB 라 유지됨.
- **auth 목업 쿠키에서 `HttpOnly` 를 뺐다.** MSW 는 HttpOnly 쿠키를 자기 저장소에만 두고 `document.cookie` 에 못 쓰므로 실제 페이지 이동에 실리지 않아, `proxy.ts` 가 목업 모드에서 `/wallet` 을 항상 `/login` 으로 튕겼다(개발 중 발견). 실서버 쿠키는 그대로 HttpOnly.

## 8. 결정 사항 (결과)

- [x] **조회 API** — `GET /users/me/wallet` 을 이 브랜치에 포함(§1-2). 처음엔 패치로 넘기려 했으나 사용자가 직접 포함하기로 결정. `/users/me`(닉네임 포함)는 별도 과제.
- [x] **SSAFY RPC CORS** — 노드가 Origin 을 반영 허용 → 브라우저 직접 호출. rewrite 없음.
- [x] **(app) 레이아웃 헤더** — `SiteNav` 재사용. `landingScene` 이 언마운트 시 `data-nav`/`data-nav-theme` 을 지우므로 기본(밝은) 색으로 정상 표시됨을 확인.
- [x] **상태 관리** — 전역 스토어 없음, 훅 파일도 없음(컴포넌트 내부 상태). 승격 시점은 §3.
- [x] **연결 교체 UX** — "해제 → 연결" 두 단계만. 교체 버튼 없음.
- [ ] **로그인 카드의 `🦊 지갑으로 연결`**(auth.md §7) — 지갑 **로그인** 은 백엔드 엔드포인트가 없어 계속 제외. `/wallet` 연결 카드가 그 역할.
- [ ] **SSAFY 네이티브 통화 심볼** — `ethereum.ts` 에 `ETH` 로 두었다(`ponytail:` 주석). 지갑에 네트워크를 새로 추가할 때만 보이는 표시용. 확인되면 교체.
- [ ] **모바일** — MetaMask 앱 브라우저에서만 동작. 연결 카드 하단 안내 한 줄만.

## 9. 검증 (2026-09-08)

- `tsc --noEmit`, `eslint src` 통과. 백엔드 `UserWalletServiceTest` 통과(`getsWallet` 포함). `UserWalletIntegrationTest.getsWallet` 은 MySQL·Redis 가 필요해 로컬 미실행.
- 목업 모드(`NEXT_PUBLIC_API_MOCKING=enabled`) + 헤드리스 Chrome(CDP, `window.ethereum` 가짜 주입)으로 흐름 완주:
  1. guest → `/wallet` → `/login?next=%2Fwallet` (proxy 가드)
  2. fixture 로그인 → `/wallet` 복귀, MetaMask 없음 안내 + 설치 링크, 네비 [지갑][로그아웃]
  3. 가짜 프로바이더 주입 → [MetaMask 지갑 연결] → `eth_requestAccounts → eth_chainId → wallet_switchEthereumChain → personal_sign` 순서 호출, 서명 원문 = 서버(목업) SIWE 문장 그대로 → 대시보드(`0xf39f…2266`, SSAFY · 31221, GSC/ETH 잔액은 실제 SSAFY RPC 값)
  4. [지갑 연결 해제] → 연결 카드
  5. 랜딩 네비: guest `서비스 소개 | 이용 방법 | GPU 목록 | 로그인`, authed `… | 지갑 | 로그아웃`
- **실서버 검증(2026-09-08, 목업 off, 로컬 백엔드 `bootRun` + 도커 MySQL/Redis)**: 테스트 계정 `wallet-test01@example.com` 가입 → 로그인 → `GET` 200 `{ walletAddress: null }` → 연결 카드 → challenge → Hardhat 테스트 키로 **실제 서명**(viem) → link 200 → 대시보드(`0xf39f…2266`) → 새로고침 후 대시보드 유지(DB) → 해제 → 연결 카드. 콘솔 오류 0.
- **남은 수동 확인**: 진짜 MetaMask 팝업 UX(계정 선택·네트워크 전환·서명 거절 문구). GSC 를 배포자 지갑에서 조금 보내 두면 0 이 아닌 잔액 확인 가능(`blockchain/README.md`).
- 참고: dev 모드에선 React Strict Mode 로 `GET` 이 마운트 시 2번 나간다(백엔드 로그). 프로덕션 빌드에선 1번.
