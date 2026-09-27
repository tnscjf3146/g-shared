-- Worker 실행 신뢰도. 과거 Job의 실패 원인 귀속은 확정할 수 없으므로 기존 행은 0부터 집계한다.
ALTER TABLE workers
    ADD COLUMN completed_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN failed_count BIGINT NOT NULL DEFAULT 0;
