# 3. DB 덤프 파일 최신본

> DB 덤프(`.sql`) 파일 자체는 **실행 중인 DB에서 추출**해야 하므로, 이 문서는 추출 방법과 배치 규칙을 안내한다.
> 최종 제출 시 아래 절차로 생성한 덤프 파일을 `exec/` 폴더에 함께 넣을 것.

## DB 정보
| 항목 | 값 |
|---|---|
| DBMS | **MySQL 8** (`docker-compose.yml` image `mysql:8`) |
| 스키마명 | `${MYSQL_DATABASE}` (예: `gshared`) |
| 마이그레이션 | Flyway (`back/src/main/resources/db/migration/` V1~V24) |
| 캐시/세션 | Redis 8 (refresh token·블랙리스트, 덤프 대상 아님) |

> 첨부 `db_dump.sql` 은 **스키마(V1~V24) + 카탈로그 시드(workloads·base_models)** 상태다 — 사용자·작업 데이터는 지갑 인증·온체인 예치가 필요해 덤프 복원 대상이 아니며, 시연 데이터는 시나리오 절차로 생성한다.

## 주요 테이블 (엔티티)
- `users` (V1) — 지갑 인증 nonce·세션은 Redis 라 별도 테이블 없음
- `workers` (V3), `jobs` (V4), `datasets` (V5)
- `workloads`, `base_models` (V6, 카탈로그 시드 포함)
- `flyway_schema_history` (마이그레이션 이력)

---

## 덤프 생성 방법

### (A) docker-compose로 MySQL이 떠 있는 경우 (권장)
```bash
# -T: 출력 리다이렉션용 / sh -c 작은따옴표: 변수를 호스트가 아니라 컨테이너 안에서 확장
docker compose exec -T mysql sh -c 'exec mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --no-tablespaces --databases "$MYSQL_DATABASE" --single-transaction --routines --triggers' > exec/db_dump.sql
```

### (B) 로컬/원격 MySQL 직접 접속
```bash
# 원격은 SSH 터널 필요(MySQL 은 루프백 바인딩): ssh -i J15E204T.pem -L 3306:127.0.0.1:3306 ubuntu@j15e204.p.ssafy.io
mysqldump -h 127.0.0.1 -P 3306 -u gshared -p \
  --databases gshared \
  --single-transaction --routines --triggers \
  > exec/db_dump.sql
```

### 스키마만 필요할 때 (구조만)
```bash
mysqldump -h 127.0.0.1 -u gshared -p --no-data gshared > exec/db_schema.sql
```

---

## 복원 방법
```bash
# 컨테이너로 복원 — 변수는 컨테이너 안에서 확장, 입력은 호스트 리다이렉션. 운영이 아닌 별도 빈 DB 에서 먼저 검증할 것
docker compose exec -T mysql sh -c 'exec mysql -u root -p"$MYSQL_ROOT_PASSWORD"' < exec/db_dump.sql
```

---

## 체크리스트
- [ ] 첨부 파일명은 `db_dump.sql` 고정 — 최신 여부는 파일 끝의 `Dump completed on <일시>` 로 확인
- [ ] `--single-transaction`으로 무결성 확보
- [ ] 민감정보(실계정 비번 등)가 데이터에 들어있으면 제출 전 확인
- [ ] 덤프 파일을 `exec/` 에 배치 후 커밋
