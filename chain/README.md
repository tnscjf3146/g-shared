# chain

GPU 작업 에스크로 컨트랙트.

```
contracts/GPUJobEscrow.sol   에스크로 본체.  함수 7개
contracts/MockSSF.sol        로컬 테스트용 토큰.  decimals 0
scripts/deploy.js            배포 + 주소·ABI 산출
scripts/e2e.js               한 바퀴 검증
abi/                         프론트·백엔드·노드가 쓰는 ABI
```

## 실행

```bash
npm install
npx hardhat test                        # 테스트 31건
npx hardhat run scripts/e2e.js          # 한 바퀴 (인메모리)
npx hardhat run scripts/deploy.js       # 배포 + ABI 산출
```

실체인에 올릴 때는 `.env` 에 개인키를 넣고 네트워크를 지정한다.

```bash
echo 'PRIVATE_KEY=0x...' > .env
npx hardhat run scripts/deploy.js --network ssafy
```

`ssafy` 네트워크에서는 `MockSSF` 를 배포하지 않고 실제 SSF 주소를 그대로 쓴다.

## 상태 전이

```
CREATED ──fundJob──▶ FUNDED ──acceptJob──▶ ASSIGNED ──submitResult──▶ SUBMITTED
   │                    │                     │                          │
   └──cancelJob─────────┘                기한 만료                  approveResult
              ▼                          의뢰자 환불                 또는 기한 만료
          CANCELLED ◀────────────────────────┘                      공급자 지급
                                                                          ▼
                                                                     COMPLETED
```

`settleTimeout` 은 호출자 제한이 없다. 컨트랙트가 시한을 직접 검사하므로 누가 불러도 결과가 같다.

## 함수

| 함수 | 부르는 사람 | 상태 전이 | 토큰 |
|---|---|---|---|
| `createJob(price)` | 의뢰자 | → CREATED | — |
| `fundJob(jobId)` | 의뢰자 | CREATED → FUNDED | 의뢰자 → 에스크로 |
| `cancelJob(jobId)` | 의뢰자 | CREATED·FUNDED → CANCELLED | 에스크로 → 의뢰자 |
| `acceptJob(jobId)` | 공급자 노드 | FUNDED → ASSIGNED | — |
| `submitResult(jobId, hash)` | 공급자 노드 | ASSIGNED → SUBMITTED | — |
| `approveResult(jobId)` | 의뢰자 | SUBMITTED → COMPLETED | 에스크로 → 공급자 |
| `settleTimeout(jobId)` | 누구나 | 시한 경과 시 분기 | 상태에 따라 |

예치는 트랜잭션 두 번이다. ERC-20 에서 승인과 이체가 분리되어 있어 `token.approve` 를 먼저 보내야 한다.

## 이벤트

```
JobCreated(jobId, client, price)
JobFunded(jobId, price)                                    공급자 노드가 구독한다
JobCancelled(jobId, client, refund)
JobAccepted(jobId, provider, deadline)
ResultSubmitted(jobId, provider, resultHash, deadline)
JobCompleted(jobId, provider, payout)
JobTimedOut(jobId, fromState)
```

블록 번호와 시각은 이벤트에 자동으로 딸려 온다. 수락·제출 시각을 컨트랙트에 따로 저장하지 않는 이유다.

## 주의

- SSF 는 `decimals = 0` 이다. 단위 변환을 하지 않는다. 1 SSF 는 정수 1 이다.
- `evmVersion` 은 `paris` 다. 기본값으로 두면 SSAFY 체인에서 배포가 실패한다.
- 돈이 나가는 함수는 상태를 먼저 바꾸고 토큰을 보낸다. 순서를 바꾸면 재진입 공격이 열린다.
- 작업 상세(workloadId · imageDigest · datasetHash)는 온체인에 두지 않는다. DB 가 원본이다.
