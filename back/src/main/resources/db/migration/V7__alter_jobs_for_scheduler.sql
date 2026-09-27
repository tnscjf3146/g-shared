-- Scheduler 연동을 위한 jobs 스키마 개편.
-- 기존 jobs 행은 개발 검증용 데이터뿐이므로 폐기한다
-- (숫자 workload_id → 문자열 전환, NOT NULL 컬럼 추가와 양립 불가).
UPDATE workers SET current_job_id = NULL;
DELETE FROM jobs;

-- ① 참조 타입 정정 (문자열 카탈로그 ID)
ALTER TABLE jobs
    MODIFY COLUMN workload_id   VARCHAR(32) NOT NULL,
    MODIFY COLUMN base_model_id VARCHAR(64) NOT NULL;

-- ② training_config JSON → 컬럼 4개 (AUTO/CUSTOM 모두 항상 채움)
ALTER TABLE jobs
    ADD COLUMN epochs        INT    NOT NULL AFTER training_mode,
    ADD COLUMN batch_size    INT    NOT NULL AFTER epochs,
    ADD COLUMN learning_rate DOUBLE NOT NULL AFTER batch_size,
    ADD COLUMN max_length    INT    NOT NULL AFTER learning_rate,
    DROP COLUMN training_config;

-- ③ Scheduler 결과 저장 + 예측값 정밀도(DOUBLE)
ALTER TABLE jobs
    ADD COLUMN scheduler_model_version VARCHAR(50) NULL AFTER predicted_runtime_sec,
    ADD COLUMN recommended_worker_id   VARCHAR(64) NULL AFTER scheduler_model_version,
    MODIFY COLUMN predicted_runtime_sec DOUBLE NOT NULL,
    MODIFY COLUMN actual_runtime_sec    DOUBLE NULL;

-- ④ 아무것도 가리키지 않는 컬럼 제거 (Worker 1개 = GPU 1개 → assigned_worker_id 가 곧 GPU)
ALTER TABLE jobs DROP COLUMN gpu_id;

-- ⑤ FK 연결 (ERD-DDL 불일치 해소)
ALTER TABLE jobs
    ADD CONSTRAINT fk_jobs_workload   FOREIGN KEY (workload_id)        REFERENCES workloads (workload_id),
    ADD CONSTRAINT fk_jobs_base_model FOREIGN KEY (base_model_id)      REFERENCES base_models (model_id),
    ADD CONSTRAINT fk_jobs_dataset    FOREIGN KEY (dataset_id)         REFERENCES datasets (dataset_id),
    ADD CONSTRAINT fk_jobs_worker     FOREIGN KEY (assigned_worker_id) REFERENCES workers (worker_id);

-- ⑥ workers.current_job_id 타입 정합 (VARCHAR → BIGINT, jobs.job_id 와 일치)
ALTER TABLE workers
    MODIFY COLUMN current_job_id BIGINT NULL;
