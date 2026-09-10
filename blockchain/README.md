# GShared Blockchain — G Coin

현재 구현 범위는 G Coin ERC-20 토큰, 단위 테스트, 로컬 및 SSAFY Ignition 배포 도구다. 2026-09-07 SSAFY 체인 배포와 온체인 검증, MetaMask 지갑 간 GSC 전송 확인을 완료했다. Escrow 구현은 다음 단계다.

## 실제 배포 기록 (2026-09-07)

| 항목 | 값 |
| --- | --- |
| 네트워크 / Chain ID | SSAFY / 31221 |
| RPC | https://rpc.ssafy-blockchain.com |
| GCoin 주소 | `0xaCe3c977A539C16668528619F9417589f12C7B22` |
| 배포자 / 초기 수령자 | `0xbEB147e269B2bc04110EE8249A0624F875a52595` |
| 최초 발행량 | 1,000,000 GSC |
| 배포 트랜잭션 | `0xe830271ac270cc8e31ab58dfb03c5ba10f7b40a7045b8d799a5e2b0a7d9c28e5` |
| 실제 배포 수수료 | 0 |

`deployments/ssafy-31221/GCoin.json`에 주소와 ABI, `deployment.json`에 배포 영수증 요약을 저장했다. 런타임 바이트코드, 토큰 정보, 초기 수령 지갑의 100만 GSC 잔액, 최초 Transfer(발행) 이벤트를 확인했다. MetaMask에 GSC를 등록하고 다른 테스트 지갑으로 전송한 뒤 수신 계정의 잔액 표시까지 확인했다.

## 토큰 정책

| 항목 | 값 |
| --- | --- |
| 컨트랙트 | `GCoin` |
| 이름 / 심볼 | `G Coin` / `GSC` |
| decimals | `18` |
| 총발행량 | `1,000,000 GSC` = `10^24` 최소 단위 |
| 초기 수령자 | 생성자 인자 `initialHolder` |
| 추가 발행 | 외부 mint 함수 없음 |
| 소각 / 관리자 권한 / 업그레이드 | 제공하지 않음 |

초기 수령자는 배포자와 다른 운영 지갑일 수 있다. 영 주소(`address(0)`)를 지정하면 OpenZeppelin ERC-20이 배포를 거부한다. 잔액, 승인 한도와 전송 금액은 모두 정수 최소 단위로 처리한다.

## 실행

Node.js 24 LTS를 권장한다. Node.js 24.18.0 / npm 11.16.0에서 검증했다. 아래 명령은 `blockchain/` 디렉터리에서 실행한다.

```shell
npm ci
npm run check
```

`check`는 컴파일 → TypeScript 타입 검사 → 테스트를 순서대로 실행한다. 생성된 ABI 타입을 사용하므로 최초 타입 검사 전에 컴파일이 필요하다. 의존성은 정확한 버전과 `package-lock.json`으로 고정한다.

```shell
npm run build
npm run typecheck
npm test
```

Solidity는 `0.8.28`, EVM 타깃은 프로젝트 설계의 `paris`를 사용한다. `artifacts/`, `cache/`, `node_modules/`는 생성물이므로 커밋하지 않는다.

## 테스트 범위

`test/GCoin.ts`는 Hardhat의 로컬 네트워크와 테스트 지갑을 사용한다.

- 토큰 이름, 심볼, decimals 및 지정 수령자의 전체 초기 물량
- 영 주소로 배포 시 실패
- 소수 단위 전송, Transfer 이벤트와 총발행량 보존
- 승인 자체로 잔액이 이동하지 않는지 및 위임 전송 후 한도 감소
- 미승인 전송, 승인 한도 초과, 잔액 초과 차단
- 위임 전송 실패 시 잔액과 승인 한도의 원상 유지
- 영 주소로 직접·위임 전송 차단
- 배포자·초기 수령자·제3자의 mint 호출 차단
- Ignition 배포 모듈의 초기 수령자 인자 적용

가스 가격 0인 로컬 HTTP RPC에서 실제 CLI 배포 경로를 검사하려면 다음을 실행한다.

```shell
npm run test:deployment
```

네이티브 잔액 0인 테스트 지갑으로 배포, 초기 100만 GSC 잔액, 개인키 없는 바이트코드/토큰 검증, 재실행 시 중복 배포 방지, 잘못된 Chain ID·영 주소·컨트랙트 주소 차단을 검사한다. 실제 SSAFY RPC는 사용하지 않는다. 테스트는 생성한 chain-31337 기록을 정리하며, 해당 기록이 이미 있으면 덮어쓰지 않고 중단한다.

## 로컬 배포

```shell
npm run deploy:local
```

기본 설정은 일회성 로컬 시뮬레이션 네트워크다. 실제 SSAFY 체인에 배포되지 않으며 프로세스 종료 후 해당 체인 상태는 유지되지 않는다. 기본 초기 수령자는 로컬 첫 번째 테스트 계정이다.

`ignition/modules/GCoin.ts`의 `GCoinModule.initialHolder` 파라미터로 다른 수령자를 지정할 수 있다. 이 경로는 테스트에서도 검증한다.


## 로컬 개발 체인 — 연동 개발용

워커와 백엔드가 온체인 연동을 개발할 때 붙을 곳이다. 에스크로는 SSAFY 체인에 배포되어 있지 않고 체인 가드가 배포를 막고 있어, 로컬에 같은 구성을 세운다.

**SSAFY 체인 대신 로컬을 쓰는 결정적인 이유는 시각을 조작할 수 있다는 점이다.** `abortJob` 과 시한 만료 경로를 시험하려면 시계를 앞으로 돌려야 하는데 SSAFY 체인에서는 못 한다. 여기서는 시한을 초 단위로 짧게 잡아 몇 분 안에 전 경로를 확인한다.

### 준비

터미널 하나에서 노드를 띄운다.

```shell
npm run dev:node
```

다른 터미널에서 컨트랙트를 올리고 계정을 준비한다.

```shell
npm run dev:chain
```

GCoin 과 에스크로가 배포되고, 의뢰자에게 10,000 GSC 가 들어가며, 소유자가 워커 키를 승인한 상태가 된다. 주소와 계정은 `deployments/localhost/dev-chain.json` 에 적힌다.

```text
acceptTimeout     60초
executionTimeout  300초
```

### 공급자 노드 흉내

```shell
npm run dev:agent          정상 흐름 — 수락 → 학습 → 제출 → 지급
npm run dev:agent:abort    실패 흐름 — 수락 → 반납 → 환불
npm run dev:agent:idle     응답 불능 — 수락하지 않음
```

`scripts/provider-agent.ts` 는 **워커가 파이썬으로 옮겨 갈 참고물**이다. 수락 전 대조 일곱 항목, 결과 해시 만드는 법, 반납 경로가 주석과 함께 들어 있다.

### 키퍼 흉내

시한이 지난 작업을 정리한다. `settleTimeout` 은 누구나 부를 수 있지만 가스가 0 이라 아무도 자동으로 부르지 않는다.

```shell
npm run dev:keeper
```

시한 만료를 시험하려면 시계를 먼저 돌린다.

```shell
curl -s -X POST http://127.0.0.1:8545 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"evm_increaseTime","params":[120]}'
curl -s -X POST http://127.0.0.1:8545 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"evm_mine","params":[]}'
```

`scripts/keeper.ts` 는 **백엔드가 web3j 로 옮길 참고물**이다. 실제로는 전수를 훑지 않고 DB 에 보관한 만료 후보만 조회하면 된다.


## ABI

`artifacts/` 는 `.gitignore` 에 있어 저장소에 남지 않는다. 연동 측은 ABI 가 있어야 호출부를 만들 수 있고, 손으로 복사하면 반드시 코드와 어긋나므로 컴파일 산출물에서 뽑아 커밋한다.

```shell
npm run abi
```

```text
blockchain/abi/GPUJobEscrow.json
```

`sourceCommit` 이 들어 있어 어느 코드에서 나온 ABI 인지 확인할 수 있다. 상태 enum 은 ABI 에 `uint8` 로만 나와 이름이 사라지므로 소스에서 읽어 함께 넣는다.

연동 규격은 `docs/03-에스크로-연동-규격.md` 다. **이 문서와 ABI 파일이 정본이다.**


## SSAFY 체인 배포

아래 명령은 `blockchain/`에서 실행한다. 실제 RPC와 Chain ID는 팀에서 확인한 값을 사용한다. 배포자와 초기 수령 지갑은 서로 달라도 된다.

### 1. 환경변수 준비

PowerShell:

```powershell
Copy-Item .env.example .env
```

이미 `.env`가 있으면 복사하지 말고 기존 파일을 수정한다. `.env`는 Hardhat 설정에서 자동으로 읽으며, 셸에 이미 설정된 환경변수가 우선한다.

| 변수 | 입력할 값 |
| --- | --- |
| `SSAFY_RPC_URL` | 실제 SSAFY HTTP(S) RPC 주소 |
| `SSAFY_CHAIN_ID` | 네트워크의 10진수 Chain ID |
| `SSAFY_PRIVATE_KEY` | 배포 지갑 개인키. `0x` + 64자리 16진수 |
| `GSC_INITIAL_HOLDER` | 100만 GSC를 받을 공개 지갑 주소. 필수이며 배포자로 자동 대체하지 않음 |
| `GSC_CONTRACT_ADDRESS` | 별도 주소를 조회할 때만 지정. 일반 배포에서는 비워 둠 |

개인키는 로컬 `.env`에만 입력하고 채팅·Git·프론트엔드에 넣지 않는다. 초기 수령자가 MetaMask에서 사용하는 계정이면 배포 후 그 계정으로 전체 물량을 확인할 수 있다.

### 2. 배포 전 점검

```powershell
npm run check
npm run preflight:ssafy
```

사전 점검은 RPC Chain ID, 초기 수령 주소, 배포 지갑, 최신 블록의 base fee, 가스 가격 0으로 배포 가스 추정이 가능한지 확인한다. 트랜잭션은 보내지 않는다. 성공하면 배포자·수령자·발행량·추정 가스를 출력하고 `ignition/parameters/ssafy.json`을 생성한다.

현재 정책에 따라 네트워크와 Ignition의 `gasPrice`를 모두 0으로 고정하고 수수료 자동 인상을 끈다. 가스 **사용량**은 발생하지만 `사용량 × 가스 가격 0 = 수수료 0`이다. 따라서 네이티브 코인 잔액이 0이라는 이유만으로 차단하지 않는다. 체인 정책이 바뀌면 설정을 먼저 재검토한다.

### 3. 실제 배포

```powershell
npm run deploy:ssafy
```

사전 점검 → Ignition 배포 → 배포 후 검증 순서로 실행한다. Ignition에서 확인을 요청하면 출력된 네트워크와 주소를 확인하고 진행한다. 이 명령은 실제 체인에 배포 트랜잭션을 전송하며 지정 지갑에 100만 GSC를 발행한다.

`default`와 `production` 컴파일 프로필 모두 Solidity 0.8.28, `paris`, optimizer 200회를 사용한다. Ignition의 기본 배포 프로필에서도 SSAFY 호환 설정이 유지된다.

배포 기록은 `ignition/deployments/chain-<Chain ID>/`에 저장된다. 기록을 유지하면 같은 모듈을 재실행할 때 기존 배포를 재사용한다. 실패 시 원인을 해결하고 같은 명령을 재실행한다. 이 폴더를 삭제하거나 다른 PC에서 기록 없이 실행하면 새 토큰이 배포될 수 있다.

### 4. 검증 및 팀 전달

```powershell
npm run verify:ssafy
```

검증은 개인키 없이도 실행할 수 있으며 트랜잭션을 보내지 않는다. RPC·Chain ID·초기 수령 주소는 필요하다. 주소는 기본적으로 Ignition 기록에서 읽고, `GSC_CONTRACT_ADDRESS`가 있으면 그 주소를 검사한다.

- 현재 빌드와 배포된 런타임 바이트코드 일치
- 이름 G Coin, 심볼 GSC, decimals 18, 총발행량 100만 개
- 설정된 초기 수령 지갑의 현재 잔액 조회 (전송 후에는 100만 개보다 적을 수 있음)

성공하면 `deployments/ssafy-<Chain ID>/GCoin.json`에 공개 주소, Chain ID, 토큰 정보, ABI를 내보낸다. 초기 수령 주소는 환경변수에서 가져온 설정값이며, 이 검증은 최초 발행 이벤트를 별도로 조회하지 않는다. 트랜잭션 상세 기록은 Ignition의 `journal.jsonl`에 남는다. 실제 배포 후 두 배포 기록을 검토해 Git에 함께 남기고 주소·ABI를 프론트/백엔드에 전달한다. 기록에 개인키를 넣지 않는다.

### 5. MetaMask 수동 확인

1. 팀에서 제공한 RPC·Chain ID·네이티브 통화 정보로 SSAFY 네트워크를 등록한다.
2. 초기 수령 지갑 계정에서 사용자 지정 토큰으로 배포된 GCoin 주소를 추가한다.
3. 심볼 `GSC`, decimals `18`, 최초 잔액 `1,000,000 GSC`를 확인한다.
4. 별도의 테스트 지갑에 `1 GSC`를 보내고 트랜잭션 성공과 양쪽 잔액을 확인한다. 전송에 사용하는 계정도 가스 가격 0 설정이 적용되어야 한다.
5. `npm run verify:ssafy`를 다시 실행해 총발행량이 유지되는지 확인한다.

로컬 테스트 통과만으로 실제 SSAFY RPC·MetaMask 호환성이 확인되지는 않는다. 위 네트워크 배포와 수동 전송을 수행한 뒤 완료로 기록한다.


## 다음 단계

GSC를 `paymentToken`으로 사용하는 Escrow를 구현하고 예치·권한 검사·정산 테스트를 추가한다. Provider 선정 방식은 팀과 확정한 정책을 컨트랙트 권한 검사에 반영한다.

## 참고

- [Hardhat 3 시작하기](https://hardhat.org/docs/getting-started)
- [OpenZeppelin ERC-20](https://docs.openzeppelin.com/contracts/5.x/erc20)
