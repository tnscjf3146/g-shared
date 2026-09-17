-- 입력 LoRA 어댑터를 Job 에 묶는다 (lora-adapter-backend-작업 §1).
--
-- 어댑터는 "이 학습을 어디서부터 시작할지"를 정하는 입력 파일이다. 수명·검증 방식이 데이터셋 원본(V15)과 같아서
-- 컬럼 모양도 그대로 맞췄다: 파일은 Job 을 돌리는 동안만 스토리지에 있다가 끝나면 cleanup 이 지운다.
--
-- adapter_required   : 주문 때 어댑터를 쓰겠다고 했는가. TRUE 인데 파일이 없으면 /fund 가 거절한다.
--                      "이어서 학습"(V18 parent_job_id)도 TRUE 지만, 그때는 부모 Job 의 결과물을 쓰므로 올릴 파일이 없다.
-- adapter_object_key : jobs/{id}/input/adapter.tar. Backend 가 정하고 프런트는 받은 값을 그대로 쓴다.
-- adapter_sha256     : 업로드 완료 검증 때 Backend 가 실제 파일에서 계산한 해시 (0x 없음). execution-context 의
--                      adapter.sha256 으로 나가 워커가 내려받은 파일을 대조한다.
-- adapter_uploaded_at: 완료 검증 시각. NULL 이면 아직 파일이 없다.
-- adapter_deleted_at : cleanup 이 지운 시각. 키는 남겨 "무엇이 있었는지"는 추적된다.
ALTER TABLE jobs
    ADD COLUMN adapter_required    BOOLEAN      NOT NULL DEFAULT FALSE AFTER dataset_deleted_at,
    ADD COLUMN adapter_object_key  VARCHAR(512) NULL     AFTER adapter_required,
    ADD COLUMN adapter_size_bytes  BIGINT       NULL     AFTER adapter_object_key,
    ADD COLUMN adapter_sha256      CHAR(64)     NULL     AFTER adapter_size_bytes,
    ADD COLUMN adapter_uploaded_at DATETIME(6)  NULL     AFTER adapter_sha256,
    ADD COLUMN adapter_deleted_at  DATETIME(6)  NULL     AFTER adapter_uploaded_at;

-- cleanup 순찰이 "어댑터 파일이 남아 있는 Job" 을 찾는 조건 (데이터셋 인덱스와 같은 모양)
CREATE INDEX idx_jobs_adapter_cleanup ON jobs (adapter_deleted_at, status);
