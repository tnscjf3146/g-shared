-- Dataset 원본 업로드를 Job 에 묶는다 (Dataset Upload Handoff).
--
-- Dataset 메타데이터(datasets 행)와 원본 파일의 수명은 다르다: 메타는 남고, 파일은 그 Job 을 돌리는 동안만
-- EC2 스토리지에 있다가 Job 이 끝나면(COMPLETED/FAILED/CANCELLED) 지워진다. 그래서 파일의 위치·검증값은
-- datasets 가 아니라 jobs 에 둔다 — 결과물(result_*)과 같은 모양이다.
--
-- dataset_object_key : jobs/{id}/input/{fileName}. Backend 가 만들고 프런트는 받은 값을 그대로 쓴다.
-- dataset_sha256     : 업로드 완료 검증 때 Backend 가 실제 파일에서 계산한 해시 (0x 없음). execution-context 의
--                      dataset.sha256 으로 나가 워커가 내려받은 파일을 대조한다.
-- dataset_uploaded_at: 완료 검증 시각. NULL 이면 아직 파일이 없어 /fund 가 거절한다.
-- dataset_deleted_at : cleanup 이 파일을 지운 시각. 키는 남겨 "무엇이 있었는지"는 추적된다.
ALTER TABLE jobs
    ADD COLUMN dataset_object_key  VARCHAR(512) NULL AFTER dataset_id,
    ADD COLUMN dataset_size_bytes  BIGINT       NULL AFTER dataset_object_key,
    ADD COLUMN dataset_sha256      CHAR(64)     NULL AFTER dataset_size_bytes,
    ADD COLUMN dataset_uploaded_at DATETIME(6)  NULL AFTER dataset_sha256,
    ADD COLUMN dataset_deleted_at  DATETIME(6)  NULL AFTER dataset_uploaded_at;

-- cleanup 순찰이 "파일이 남아 있는 Job" 을 찾는 조건
CREATE INDEX idx_jobs_dataset_cleanup ON jobs (dataset_deleted_at, status);
