-- Escrow 규격 회신 반영 (2026-09-11).
-- ① CANCELLING 상태 추가 — 배정·실행 중 취소는 워커의 abortJob()·JOB_CANCELLED 확인 뒤에야 CANCELLED 로 확정한다.
--    status 컬럼은 VARCHAR(20) 문자열이라 스키마 변경 없이 값만 늘어난다.
-- ② 온체인 상태와 DB 가 어긋난 Job 표시. 자동 재매칭 대신 사람이(또는 후속 reconciliation 작업이) 온체인을 보고 정리한다.
--    값이 있으면 "대조 필요", NULL 이면 정상.
ALTER TABLE jobs
    ADD COLUMN reconciliation_reason TEXT NULL AFTER fail_reason;
