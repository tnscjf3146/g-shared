-- System(Worker) ↔ Backend 연동 규격 v1 반영.
-- 워커가 온체인 서명 주소(operator)를 갖고, Job 은 에스크로·결과물 업로드·트랜잭션 흔적을 기록한다.

-- ① workers — 등록 규격 변경 (operatorAddress · clientVersion · system.os/wslAvailable · benchmarkVersion)
--    device_name / driver_version 은 새 규격에 없지만 옛 행이 남아 있어 컬럼은 유지한다(NULL 허용).
ALTER TABLE workers
    ADD COLUMN operator_address  CHAR(42)    NULL AFTER worker_token,   -- Worker 가 트랜잭션에 서명하는 공개 주소 (개인키는 절대 저장하지 않는다)
    ADD COLUMN client_version    VARCHAR(32) NULL AFTER operator_address,
    ADD COLUMN wsl_available     BOOLEAN     NULL AFTER os_version,
    ADD COLUMN benchmark_version VARCHAR(64) NULL AFTER benchmark_tokens_per_sec;

-- ② jobs — 에스크로 대조값 · 트랜잭션 해시 · 결과물 오브젝트 · 진행률 상세
ALTER TABLE jobs
    -- 온체인 createAndFundJob(externalRef, ...) 에 넣는 참조값. Backend 가 생성 시 발급한다 (bytes32 hex).
    ADD COLUMN external_ref      CHAR(66)     NULL AFTER estimated_cost,
    -- 온체인 jobId (uint256). 예치 확인 시 기록 — JSON 에서는 문자열로 다룬다.
    ADD COLUMN escrow_job_id     VARCHAR(78)  NULL AFTER external_ref,
    ADD COLUMN accept_tx_hash    CHAR(66)     NULL AFTER assigned_worker_id,   -- acceptJob()
    ADD COLUMN submit_tx_hash    CHAR(66)     NULL AFTER result_url,           -- submitResult()
    ADD COLUMN abort_tx_hash     CHAR(66)     NULL AFTER submit_tx_hash,       -- abortJob() (실패·취소)
    ADD COLUMN result_object_key VARCHAR(512) NULL AFTER result_hash,          -- 스토리지 오브젝트 키 (jobs/{id}/result.tar)
    ADD COLUMN result_size_bytes BIGINT       NULL AFTER result_object_key,
    ADD COLUMN result_sha256     CHAR(64)     NULL AFTER result_size_bytes,    -- 업로드 완료 시 검증한 파일 해시 (0x 없음)
    ADD COLUMN progress_step        INT NULL AFTER progress_percent,
    ADD COLUMN progress_total_steps INT NULL AFTER progress_step,
    ADD COLUMN progress_elapsed_sec INT NULL AFTER progress_total_epochs,
    -- progressPercent = currentStep / totalSteps × 100 — 규격상 소수(43.7)
    MODIFY COLUMN progress_percent DOUBLE NULL,
    ADD CONSTRAINT uk_jobs_external_ref UNIQUE (external_ref);

-- ③ base_models — Worker 가 실제로 내려받을 Hugging Face 참조
ALTER TABLE base_models
    ADD COLUMN model_ref      VARCHAR(255) NULL AFTER display_name,   -- "Qwen/Qwen2.5-7B"
    ADD COLUMN model_revision VARCHAR(64)  NULL AFTER model_ref;      -- 고정 커밋 SHA (없으면 main)

UPDATE base_models
   SET model_ref = 'Qwen/Qwen2.5-7B'
 WHERE slug = 'qwen2.5-7b';
-- TODO: model_revision 은 System 담당이 검증한 커밋 SHA 로 고정할 것

-- ④ workloads — imageRef = image_repository@image_digest
ALTER TABLE workloads
    ADD COLUMN image_repository VARCHAR(255) NULL AFTER image_digest;

UPDATE workloads
   SET image_repository = 'gshared/qlora-sft-runtime'
 WHERE slug = 'qlora-sft-v1';
