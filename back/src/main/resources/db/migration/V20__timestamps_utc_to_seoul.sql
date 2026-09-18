-- 서버가 만든 시각을 UTC → Asia/Seoul 로 보정한다.
--
-- 왜: 배포 컨테이너(eclipse-temurin, TZ 미설정)가 UTC 로 돌아 LocalDateTime.now() 와 JPA @CreatedDate 가 UTC 벽시계를
-- 저장해 왔다. 컬럼은 DATETIME(존 없음), JSON 도 존 없이 나가고, 브라우저(KST)는 그 문자열을 로컬 시각으로 읽는다 —
-- 그래서 화면의 모든 서버 시각이 9시간 빠르게(전날 밤처럼) 보였다. 같은 배포에서 컨테이너를 Asia/Seoul 로 옮기므로
-- (back/Dockerfile ENV TZ), 그 전에 쓰인 값만 한 번 밀어야 앞뒤가 맞는다.
--
-- 얼마나: ${tz_shift_hours} 시간. 이 값은 spring.flyway.placeholders.tz_shift_hours 로 프로필마다 다르다 —
--   prod = 9  (지금까지 UTC JVM 이 썼으니 밀어야 한다)
--   기본 = 0  (로컬·테스트는 호스트가 이미 KST 라 밀면 틀어진다 → no-op)
-- 값이 빠지면 Flyway 가 "No value provided for placeholder" 로 기동을 거부한다. 조용히 틀어지는 길은 없다.
--
-- 어디를: 애플리케이션이 쓰는 DATETIME 컬럼 전부(6 테이블 22 컬럼). 셋 다 같은 이유로 UTC 다 —
--   · JPA/서비스가 LocalDateTime.now() 로 쓴 값 (JVM UTC)
--   · 시드(V6·V8·V12)의 NOW(6) (MySQL 컨테이너도 UTC)
--   · DB 기본값(DEFAULT CURRENT_TIMESTAMP)은 어느 컬럼에도 없다
-- NULL 은 NULL 로 남는다(DATE_ADD(NULL) = NULL). 행 수는 바뀌지 않는다.
--
-- 재발 방지는 이 파일 몫이 아니다: 근본적으로는 LocalDateTime 대신 Instant(존 있는 절대시각)를 저장·전송해야 인프라 TZ 와 무관해진다.

UPDATE users
   SET created_at = DATE_ADD(created_at, INTERVAL ${tz_shift_hours} HOUR),
       updated_at = DATE_ADD(updated_at, INTERVAL ${tz_shift_hours} HOUR);

UPDATE workers
   SET last_heartbeat_at = DATE_ADD(last_heartbeat_at, INTERVAL ${tz_shift_hours} HOUR),
       created_at        = DATE_ADD(created_at,        INTERVAL ${tz_shift_hours} HOUR),
       updated_at        = DATE_ADD(updated_at,        INTERVAL ${tz_shift_hours} HOUR);

UPDATE workloads
   SET created_at = DATE_ADD(created_at, INTERVAL ${tz_shift_hours} HOUR),
       updated_at = DATE_ADD(updated_at, INTERVAL ${tz_shift_hours} HOUR);

UPDATE base_models
   SET created_at = DATE_ADD(created_at, INTERVAL ${tz_shift_hours} HOUR),
       updated_at = DATE_ADD(updated_at, INTERVAL ${tz_shift_hours} HOUR);

UPDATE datasets
   SET uploaded_at = DATE_ADD(uploaded_at, INTERVAL ${tz_shift_hours} HOUR),
       expires_at  = DATE_ADD(expires_at,  INTERVAL ${tz_shift_hours} HOUR),
       created_at  = DATE_ADD(created_at,  INTERVAL ${tz_shift_hours} HOUR),
       updated_at  = DATE_ADD(updated_at,  INTERVAL ${tz_shift_hours} HOUR);

UPDATE jobs
   SET dataset_uploaded_at = DATE_ADD(dataset_uploaded_at, INTERVAL ${tz_shift_hours} HOUR),
       dataset_deleted_at  = DATE_ADD(dataset_deleted_at,  INTERVAL ${tz_shift_hours} HOUR),
       adapter_uploaded_at = DATE_ADD(adapter_uploaded_at, INTERVAL ${tz_shift_hours} HOUR),
       adapter_deleted_at  = DATE_ADD(adapter_deleted_at,  INTERVAL ${tz_shift_hours} HOUR),
       started_at          = DATE_ADD(started_at,          INTERVAL ${tz_shift_hours} HOUR),
       finished_at         = DATE_ADD(finished_at,         INTERVAL ${tz_shift_hours} HOUR),
       refund_confirmed_at = DATE_ADD(refund_confirmed_at, INTERVAL ${tz_shift_hours} HOUR),
       created_at          = DATE_ADD(created_at,          INTERVAL ${tz_shift_hours} HOUR),
       updated_at          = DATE_ADD(updated_at,          INTERVAL ${tz_shift_hours} HOUR);
