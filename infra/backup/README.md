# 운영 데이터 백업

배포 서버(`j15e204.p.ssafy.io`)의 MySQL 과 오브젝트 스토리지를 내려받는 절차다.
`infra/nginx/` 와 마찬가지로 **서버 운영 기록본**이며, 저장소에 있다고 해서 자동으로 실행되지 않는다.

## 왜 필요한가

DB 에는 **혼자서 다시 만들 수 없는 것**이 들어 있다.

| 잃는 것 | 복구 방법 | 비용 |
| --- | --- | --- |
| `workers.benchmark_tokens_per_sec` | 공급자가 **각자 자기 PC 에서** 다시 등록 | 사람을 다시 모아야 한다 |
| 완주한 작업 기록과 `storage/` 의 결과 어댑터 | 실제로 GPU 학습을 다시 완주 | 수십 분, 성공 보장 없음 |
| `users` 의 지갑 연결 | 각자 다시 서명 | 사람마다 요청 |
| `jobs.escrow_job_id` 대응표 | **불가능** | `externalRef` 가 난수라 체인에서 되짚을 수 없다 |

벤치마크는 워커가 측정하지 못하면 등록 자체가 거부되도록 설계되어 있다. 대체 평균도, GPU
모델별 표도 없다. 그래서 **값을 잃으면 사람을 다시 부르는 것 외에 방법이 없다.**

그리고 완주 기록은 "이 시스템이 실제로 끝까지 돈다"는 증거물이다. 이것이 없으면 시연 당일
실시간 성공에 전부를 걸어야 한다.

### 체인이 있는데 왜 백업하는가

에스크로 컨트랙트가 지키는 것은 **돈**이다. 서버가 통째로 사라져도 의뢰자는 자기 지갑으로
`cancelJob` 을 부르거나, 시한이 지났으면 누구든 `settleTimeout` 을 불러 환불받을 수 있다.
`settleTimeout` 에는 접근 제어가 없고 환불은 언제나 `job.client` 로 간다.

백업이 지키는 것은 그 바깥이다. 어떤 모델과 데이터셋으로 학습했는지, 워커 사양과 벤치마크,
사용자 계정, 결과 파일. 애초에 체인에 올린 적 없고 올릴 이유도 없는 것들이다.

## 무엇을 백업하는가

| 대상 | 위치 | 대략 크기 |
| --- | --- | --- |
| MySQL `gshared` 스키마 | 컨테이너 `gshared-mysql` | 수십 KB |
| 오브젝트 스토리지 | `~/S15P21E204/storage` | 수백 MB |

**하지 않는 것**

- Redis. 리프레시 토큰뿐이라 잃으면 다시 로그인하면 된다.
- 도커 이미지. 레지스트리에 있다.
- 소스 코드. GitLab 에 있다.
- 체인 데이터. 체인이 원본이다.
- MySQL 계정과 권한. 그것은 `mysql` 시스템 데이터베이스에 있어 이 덤프에 들어가지 않는다.
  컨테이너를 새로 만들면 compose 의 `MYSQL_USER`·`MYSQL_PASSWORD` 가 다시 만들어 준다.

## 언제 돌리는가

| 시점 | 이유 |
| --- | --- |
| Flyway 마이그레이션 직전 | 마이그레이션 실패가 가장 흔한 손실 경로다 |
| 시연 직전 | 최신 상태 확보 |
| 큰 변경을 배포하기 전 | 되돌릴 자리를 만들어 둔다 |

**cron 을 걸지 않는다.** 알림이 없는 상태에서 정기 실행을 걸면 "백업이 있다고 믿는데 두 달째
실패 중"인 상황이 만들어진다. 없는 것보다 나쁘다. 관제와 알림이 생긴 뒤에 다시 논의한다.

## 실행

서버에 접속한 뒤 실행한다.

```bash
ssh -i ~/.ssh/J15E204T.pem ubuntu@j15e204.p.ssafy.io
~/S15P21E204/infra/backup/backup.sh
```

그리고 **노트북에서** 내려받아야 끝난다. 서버에만 있는 사본은 서버와 함께 죽는다.

```bash
mkdir -p ~/backups
scp -i ~/.ssh/J15E204T.pem 'ubuntu@j15e204.p.ssafy.io:~/backup-*' ~/backups/
```

### 서비스에 주는 영향

없다. 둘 다 읽기 작업이고 `--single-transaction` 이 테이블을 잠그지 않는다.

다만 **배포와 겹치면 안 된다.** Flyway 마이그레이션은 DDL 이라 트랜잭션 스냅샷에 들어가지
않는다. 덤프 도중에 배포가 돌면 스키마와 데이터가 어긋난 파일이 나온다. 배포는 GitLab 웹훅으로
자동 실행되므로 팀원이 머지하는 순간 예고 없이 온다.

## 복원

### 먼저 리허설로 확인한다

운영 데이터베이스를 건드리지 않고 임시 데이터베이스에 부어 본다.

```bash
docker exec gshared-mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -e "CREATE DATABASE restore_test"'

docker exec -i gshared-mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot restore_test' < backup-db-<STAMP>.sql

docker exec gshared-mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -e "SELECT COUNT(*) FROM restore_test.workers"'

docker exec gshared-mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -e "DROP DATABASE restore_test"'
```

`docker exec` 에 `-i` 가 붙는 것은 파일을 표준 입력으로 흘려 넣기 때문이다.

리허설은 `CREATE DATABASE` 를 쓰므로 **읽기 작업이 아니다.** 배포가 도는 중에는 하지 않는다.

### 실제 복원

```bash
docker exec -i gshared-mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot gshared' < backup-db-<STAMP>.sql

sudo tar xzf backup-storage-<STAMP>.tar.gz -C ~/S15P21E204
```

덤프 앞머리에 `DROP TABLE IF EXISTS` 가 붙어 있다. **복원은 곧 덮어쓰기다.** 이 파일을 운영
DB 에 부으면 현재 데이터가 사라지고 백업 시점으로 되돌아간다.

## 취급 주의

`.sql` 에는 `users` 테이블이 통째로 들어간다. 컬럼에 `email`, `password`, `wallet_address` 가
있다. 스크립트가 파일 권한을 `600` 으로 조이지만 그것으로 끝이 아니다.

- **저장소에 커밋하지 않는다.** 루트 `.gitignore` 에 `backup-*.sql` 과 `backup-*.tar.gz` 를
  넣어 두었다. 사람의 주의가 아니라 구조로 막는다.
- 메신저나 이슈에 첨부하지 않는다.

개인키는 들어가지 않는다. keeper 키는 환경변수이고 워커의 operator 키는 공급자 PC 에 있다.

## 오래된 백업 정리

**스크립트는 아무것도 지우지 않는다.** 보관 정책을 자동화하려면 "오래된 것을 지운다"는 코드를
써야 하는데, 거기에 버그가 나면 백업을 지키려고 만든 도구가 백업을 없앤다.

필요하면 사람이 확인하고 지운다. 서버 디스크 여유는 넉넉하다.

```bash
ls -lh ~/backup-*
```
