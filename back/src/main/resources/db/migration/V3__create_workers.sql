CREATE TABLE workers (
    worker_id                VARCHAR(64)  NOT NULL,
    owner_id                 BIGINT       NOT NULL,
    worker_token             VARCHAR(255) NOT NULL,
    device_name              VARCHAR(255) NULL,
    os_version               VARCHAR(100) NULL,
    gpu_model                VARCHAR(255) NULL,
    vram_mb                  INT          NULL,
    driver_version           VARCHAR(100) NULL,
    benchmark_tokens_per_sec DOUBLE       NULL,
    status                   VARCHAR(20)  NOT NULL,
    current_job_id           VARCHAR(64)  NULL,
    last_heartbeat_at        DATETIME(6)  NULL,
    created_at               DATETIME(6)  NOT NULL,
    updated_at               DATETIME(6)  NOT NULL,
    PRIMARY KEY (worker_id),
    CONSTRAINT uk_workers_token UNIQUE (worker_token),
    CONSTRAINT fk_workers_owner FOREIGN KEY (owner_id) REFERENCES users (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci;
