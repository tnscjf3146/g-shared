# GPU 공유 서비스 Blockchain 최소 스켈레톤

## 1. 목적

이 문서는 SSAFY Blockchain 위에서 동작하는 GPU 공유 서비스의 블록체인 모듈 최소 구조를 정의한다.

현재 단계의 목표는 프론트엔드, 백엔드, AI 서버 없이도 다음 흐름을 로컬 Hardhat 네트워크에서 완주하는 것이다.

```text
GPUC 배포
→ Escrow 배포
→ Client 작업 생성
→ GPUC 사용 승인
→ 작업비 예치
→ Provider 작업 수락
→ 결과 해시 제출
→ Client 결과 승인
→ Provider 정산
```

## 2. 최소 디렉터리 구조

```text
blockchain/
├─ contracts/
│  ├─ token/
│  │  ├─ GPUCreditToken.sol
│  │  └─ GPUCreditToken.t.sol
│  └─ escrow/
│     ├─ GPUJobEscrow.sol
│     └─ GPUJobEscrow.t.sol
│
├─ test/
│  └─ GPUProtocol.e2e.ts
│
├─ ignition/
│  └─ modules/
│     └─ GPUProtocol.ts
│
├─ hardhat.config.ts
├─ package.json
├─ package-lock.json
├─ tsconfig.json
├─ .env.example
├─ .gitignore
└─ README.md
```

전체 서비스가 하나의 저장소를 사용하는 경우 위 구조를 `blockchain/` 아래에 둔다. 블록체인 전용 저장소를 사용하는 경우 `blockchain/`을 생략하고 현재 저장소 루트를 기준으로 사용한다.

## 3. 파일별 역할

| 파일 | 역할 |
| --- | --- |
| `GPUCreditToken.sol` | GPU 연산 결제용 ERC-20 토큰 GPUC를 정의한다. |
| `GPUCreditToken.t.sol` | GPUC의 발행량, 전송, 승인과 권한을 단위 테스트한다. |
| `GPUJobEscrow.sol` | GPU 작업 상태, 토큰 예치, 결과 제출과 정산을 처리한다. |
| `GPUJobEscrow.t.sol` | Escrow의 상태 전이, 권한, 잔액과 이벤트를 단위 테스트한다. |
| `GPUProtocol.e2e.ts` | Client와 Provider 관점에서 전체 작업 흐름을 통합 테스트한다. |
| `GPUProtocol.ts` | GPUC와 Escrow를 같은 배포 과정에서 생성하는 Hardhat Ignition 모듈이다. |
| `hardhat.config.ts` | Solidity 버전, EVM 버전과 네트워크 설정을 관리한다. |
| `.env.example` | 필요한 환경변수 이름만 예시로 제공한다. 실제 값과 개인키는 포함하지 않는다. |

## 4. GPUCreditToken 기본 방향

GPUCreditToken은 SSAFY Blockchain 위에 배포되는 서비스 전용 ERC-20 토큰이다. 새로운 블록체인을 만드는 것이 아니라 기존 SSAFY 체인을 사용한다.

초기 권장값은 다음과 같다. 토큰 경제 정책이 확정되기 전까지는 테스트용 설정으로 취급한다.

| 항목 | 초기값 |
| --- | --- |
| 이름 | `GPU Credit` |
| 심볼 | `GPUC` |
| decimals | `18` |
| 초기 발행량 | `1,000,000 GPUC` |
| 초기 수령자 | 배포자 지갑 |

MVP에서는 OpenZeppelin ERC-20 구현을 사용하고 초기 물량을 배포자에게 발행한다. Client 테스트 지갑에는 배포자가 필요한 만큼 전송한다. 공개 Faucet과 추가 발행 권한은 실제 요구사항이 생긴 후 별도 설계한다.

## 5. GPUJobEscrow 기본 방향

Escrow는 특정 토큰 이름에 종속되지 않도록 결제 토큰을 생성자에서 받는다.

```solidity
IERC20 public immutable paymentToken;
```

MVP 상태는 다음과 같다.

```solidity
enum JobState {
    NONE,
    CREATED,
    FUNDED,
    ASSIGNED,
    SUBMITTED,
    COMPLETED
}
```

핵심 함수는 다음과 같다.

| 함수 | 호출자 | 역할 |
| --- | --- | --- |
| `createJob(uint256 price)` | Client | 가격을 지정해 작업을 생성한다. |
| `fundJob(uint256 jobId)` | Client | 승인된 GPUC를 Escrow에 예치한다. |
| `acceptJob(uint256 jobId)` | Provider | 예치가 완료된 작업을 수락한다. |
| `submitResult(uint256 jobId, bytes32 resultHash)` | Provider | 결과 파일의 해시를 제출한다. |
| `approveJob(uint256 jobId)` | Client | 결과를 승인하고 Provider 정산을 실행한다. |

상태 전이는 다음 순서를 따른다.

```text
CREATED → FUNDED → ASSIGNED → SUBMITTED → COMPLETED
```

환불, 취소, 제한 시간, 분쟁과 추가 결제는 정상 E2E 흐름이 완성된 다음 단계에서 추가한다.

## 6. 테스트 전략

### Solidity 단위 테스트

개별 컨트랙트 규칙은 `*.t.sol` 파일에서 검증한다.

- 정상 상태 전이
- 잘못된 호출자 차단
- GPUC 승인 없이 예치할 수 없는지 확인
- 예치되지 않은 작업을 Provider가 수락할 수 없는지 확인
- Provider가 아닌 주소의 결과 제출 차단
- Client가 아닌 주소의 결과 승인 차단
- 중복 정산 차단
- Client, Escrow, Provider의 최종 GPUC 잔액 확인
- 주요 이벤트 발생 확인

### TypeScript 통합 테스트

`GPUProtocol.e2e.ts`는 Hardhat 3의 `network.create()`와 viem을 사용한다. 배포에는 `viem.deployContract`, 기존 컨트랙트 연결에는 `viem.getContractAt`을 사용한다.

TypeScript 통합 테스트는 두 컨트랙트와 여러 지갑이 참여하는 전체 흐름만 담당한다. 개별 조건의 세부 검증은 Solidity 단위 테스트에서 처리해 역할 중복을 줄인다.

## 7. 검증 명령

컴파일 후 생성된 ABI 타입을 기준으로 타입 검사를 실행하고, 마지막에 전체 테스트를 수행한다.

```shell
npx hardhat build
npx tsc --noEmit
npx hardhat test
```

필요하면 테스트 계층을 나누어 실행한다.

```shell
npx hardhat test solidity
npx hardhat test nodejs
```

SSAFY Blockchain 배포 호환성을 위해 Solidity 컴파일 설정의 `evmVersion`은 `paris`를 유지한다.

## 8. 환경변수와 보안

`.env.example`에는 변수 이름과 빈 값만 작성한다.

```dotenv
SSAFY_RPC_URL=https://rpc.ssafy-blockchain.com
SSAFY_PRIVATE_KEY=
```

- 실제 `.env`는 Git에 커밋하지 않는다.
- Private Key와 Seed Phrase를 프론트엔드에 넣지 않는다.
- Backend가 사용자 대신 자금 트랜잭션을 서명하지 않는다.
- Client와 Provider의 실제 상태 변경은 각 사용자 MetaMask에서 서명한다.
- 배포용 개인키는 로컬 배포 스크립트 또는 안전한 CI Secret에서만 사용한다.

## 9. 지금 만들지 않는 항목

아래 항목은 현재 최소 스켈레톤에서 제외한다.

| 항목 | 추가 시점 |
| --- | --- |
| `MockPaymentToken.sol` | 특수한 ERC-20 동작을 별도로 시험해야 할 때 |
| `scripts/e2e-job-flow.ts` | 실제 SSAFY 체인에서 반복 실행할 E2E 스크립트가 필요할 때 |
| `scripts/verify-deployment.ts` | 실제 배포 주소 검증을 자동화할 때 |
| `scripts/export-abi.ts` | 프론트엔드와 백엔드에 ABI를 전달할 때 |
| `deployments/` | 실제 SSAFY 체인 배포 주소와 트랜잭션이 생성된 후 |
| `abi/` | 컨트랙트 인터페이스가 확정된 후 |
| 상세 `docs/` | 함수, 이벤트와 상태 정의가 확정된 후 |

빈 폴더만 미리 만들지 않고, 실제 파일이 필요한 단계에서 함께 추가한다.

## 10. 단계별 확장 순서

1. `GPUCreditToken.sol`과 단위 테스트를 작성한다.
2. 기존 `GPUJobEscrow.sol`의 `ssfToken`을 `paymentToken`으로 일반화한다.
3. Escrow 단위 테스트를 GPUC 기준으로 변경한다.
4. `GPUProtocol.e2e.ts`로 전체 로컬 흐름을 검증한다.
5. `GPUProtocol.ts` Ignition 배포 모듈을 작성한다.
6. SSAFY Blockchain에 GPUC와 Escrow를 배포한다.
7. 배포 후 `deployments`, `abi`, 연동 문서를 추가한다.
8. 프론트엔드와 백엔드가 확정된 ABI와 주소를 사용해 연동한다.

## 11. 현재 프로젝트 이전 원칙

- 기존 `GPUJobEscrow.sol`은 기능을 유지하면서 `contracts/escrow/`로 이동한다.
- 기존 `MockSSF.sol`은 GPUC 테스트 전환이 완료될 때까지만 유지한다.
- 기존 SSF 배포 및 검증 스크립트는 GPUC 실제 배포가 완료된 뒤 제거하거나 `legacy`로 분리한다.
- 임시 `frontend/`, `server/` 코드는 블록체인 기능 MR에 포함하지 않는다.
- 기존 실제 SSAFY 체인 컨트랙트는 삭제할 수 없으므로 기록으로만 보존한다.

## 12. 최소 완료 기준

다음 조건을 모두 충족하면 블록체인 최소 모듈이 완성된 것으로 본다.

- GPUC와 Escrow가 오류 없이 컴파일된다.
- Solidity 단위 테스트가 모두 통과한다.
- TypeScript E2E 테스트가 모두 통과한다.
- Client가 예치한 금액과 Provider가 받은 금액이 정확히 일치한다.
- 중복 지급과 권한 없는 상태 변경이 차단된다.
- 배포 과정에서 Private Key가 코드나 로그에 노출되지 않는다.
- 프론트엔드와 백엔드가 사용할 함수, 이벤트와 ABI가 확정된다.
