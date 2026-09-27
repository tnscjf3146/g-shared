# 실패 작업 환불 복구 — MR 105 후속 변경

## 적용 범위

기준 커밋은 MR 105의 `b2ab6bb`다. MR 105를 먼저 반영하거나 같은 브랜치에 이 후속 변경을 반영한다.
컨트랙트와 워커 바이너리 변경은 필요하지 않다. 백엔드 배포와 V16 DB 마이그레이션이 필요하다.
현재 저장소의 develop 작업 폴더와 운영 DB/체인에는 이 변경을 적용하지 않았다.

MR 105는 환불 미확인 `JOB_FAILED`를 `CANCELLING`에 남긴다. 이 후속 변경은 이미 `FAILED` 또는
`CANCELLED`로 닫힌 미환불 작업과, 수락 전 `JOB_REJECTED`로 끝난 작업을 복구한다.

## 동작

1. `refund_confirmed_at`이 비어 있고 에스크로 ID가 있는 FAILED/CANCELLED 작업을 조회한다.
2. 저장된 chainId와 RPC 체인, 당시 계약 주소, 의뢰자, externalRef→escrowJobId 매핑,
   공급자, 금액을 대조한다. 주소가 없는 과거 행을 현재 계약 주소로 추측하지 않는다.
3. 체인이 FUNDED/ASSIGNED이면 CANCELLING으로 복구한다. 학습 재실행이나 재매칭은 하지 않는다.
4. 기존 keeper가 온체인 deadline 만료 후 settleTimeout을 전송한다. 영수증 성공을 확인한 뒤 닫는다.
5. 이미 체인이 CANCELLED인 작업은 환불 확인 시각만 기록한다. COMPLETED는 이미 지급된 작업이므로
   자동 환불하지 않고 대조 필요 사유를 남긴다. RPC 장애/불일치는 다음 순찰 또는 수동 확인으로 남긴다.

복구 순찰은 시작 후 20초, 이후 실행 종료부터 120초 간격이다. keeper는 기존 설정대로 시작 후
45초, 실행 종료부터 60초 간격이며 RPC 실패 시 백오프한다. 따라서 실시간 환불을 보장하는 주기는 아니다.
환불 확인 시각은 **블록 시각이 아닌 백엔드 확인 시각**이다.

환불 정리로 원래 실패 사유와 기존 abortTxHash를 덮어쓰지 않는다. 취소 중 들어온 실패도 원인을 저장한다.
지연·중복 실패/취소 보고가 도착해도 워커가 다른 작업을 수행 중이면 그 워커를 ONLINE으로 풀지 않는다.

## 운영 적용

1. 배포 대상이 MR 105와 이 변경을 모두 포함하는지 확인한다.
2. 배포 Secret에 아래 값을 설정한다. 저장소 루트의 compose는 `.env`를 backend에 전달한다.
   개인키 값을 로그, MR, 문서에 넣지 않는다.

   - `ESCROW_CONTRACT_ADDRESS`: 실제 배포된 에스크로
   - `ESCROW_RPC_URL`: 해당 체인의 RPC
   - `WALLET_AUTH_CHAIN_ID`: SSAFY는 31221
   - `GSC_TOKEN_ADDRESS`: 해당 에스크로의 paymentToken과 같은 토큰
   - `ESCROW_VERIFICATION_REQUIRED=true`
   - `ESCROW_KEEPER_PRIVATE_KEY`: 만료 환불용 지갑의 키. 워커 operator 키가 필요하지 않다.

3. 백엔드를 배포한다. Flyway V16은 nullable 확인 시각과 인덱스만 추가하며 기존 작업 상태를 일괄 변경하지 않는다.
4. 기동 로그의 `keeper=true`를 확인한다. 키가 없으면 자동 만료 환불이 비활성화됐다는 경고가 나온다.
5. `[REFUND] ... CANCELLING으로 복구`, `[KEEPER] settleTimeout 전송`,
   `[RECONCILE] 체인 CANCELLED` 로그와 작업의 실제 지갑 잔액/온체인 상태를 대조한다.

배포 후 읽기 전용 점검 SQL:

```sql
SELECT job_id, status, escrow_chain_id, escrow_contract_address, escrow_job_id,
       abort_tx_hash, refund_confirmed_at, fail_reason, reconciliation_reason
FROM jobs
WHERE escrow_job_id IS NOT NULL
  AND refund_confirmed_at IS NULL
  AND status IN ('FAILED', 'CANCELLED', 'CANCELLING')
ORDER BY job_id;
```

`REFUND_RECOVERY: ... mismatch` 또는 `missing ... binding`은 자동 복구하지 않는다.
예치 트랜잭션의 JobFunded 이벤트와 원래 주문을 대조해 정확한 식별 정보를 복원해야 한다.
**FAILED 전체를 CANCELLING으로 바꾸는 일괄 UPDATE는 사용하지 않는다.**

## 환불 시점과 한계

워커가 살아 있고 자신의 ASSIGNED 작업에 abortJob을 성공시키면 바로 환불된다.
워커가 응답하지 않으면 이 변경은 기존 settleTimeout으로 회수한다.
저장된 SSAFY 배포 기록은 수락 전 600초, 수락 후 108000초(30시간)이다. 실제 판단은 해당 작업의 온체인
`deadline`과 최신 블록 시각을 사용한다. 실패 발생부터 30시간을 새로 세는 것이 아니다.

이 변경은 워커의 재시작 후 abortJob 재시도를 구현하지 않는다. 즉시 환불 재시도는 별도 워커 개선 사항이다.
온체인 COMPLETED로 이미 공급자에게 지급된 금액은 이 에스크로의 환불 경로로 돌려받을 수 없다.

## 로컬 검증

Java 21에서 백엔드 디렉터리 기준:

```powershell
./gradlew.bat test --tests '*EscrowRefundRecoveryTest' --tests '*JobRefundRecoveryRepositoryTest' --tests '*JobFailureWorkerReleaseTest' --tests '*EscrowFundingVerificationTest'
```

테스트는 H2 인메모리 DB와 RPC 대역을 사용한다. 운영 DB나 실제 체인에 환불 트랜잭션을 보내지 않는다.
H2 검증은 운영 MySQL에서의 전체 Flyway 마이그레이션 검증을 대신하지 않는다.
