CREATE TABLE jobs (
    job_id                BIGINT       NOT NULL AUTO_INCREMENT,
    owner_id              BIGINT       NOT NULL,

    -- 참조 (해당 도메인 테이블은 추후 생성 → 지금은 FK 없이 ID만 보관)
    workload_id           BIGINT       NOT NULL,
    base_model_id         BIGINT       NOT NULL,
    dataset_id            BIGINT       NOT NULL,
    gpu_id                BIGINT       NOT NULL,

    training_mode         VARCHAR(10)  NOT NULL,           -- AUTO / CUSTOM
    training_config       TEXT         NULL,               -- CUSTOM 학습설정 JSON

    predicted_runtime_sec INT          NOT NULL,
    estimated_cost        BIGINT       NOT NULL,
    actual_runtime_sec    INT          NULL,
    actual_cost           BIGINT       NULL,

    status                VARCHAR(20)  NOT NULL,           -- JobStatus

    assigned_worker_id    VARCHAR(64)  NULL,               -- 배정된 Worker (workers.worker_id)
    result_hash           VARCHAR(255) NULL,
    result_url            VARCHAR(500) NULL,

    -- 진행률 스냅샷 (JOB_PROGRESS 수신 시 갱신)
    progress_percent      INT          NULL,
    progress_epoch        INT          NULL,
    progress_total_epochs INT          NULL,

    started_at            DATETIME(6)  NULL,
    finished_at           DATETIME(6)  NULL,
    created_at            DATETIME(6)  NOT NULL,
    updated_at            DATETIME(6)  NOT NULL,

    PRIMARY KEY (job_id),
    KEY idx_jobs_owner_status (owner_id, status),   -- 목록 상태필터 조회용
    CONSTRAINT fk_jobs_owner FOREIGN KEY (owner_id) REFERENCES users (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci;
