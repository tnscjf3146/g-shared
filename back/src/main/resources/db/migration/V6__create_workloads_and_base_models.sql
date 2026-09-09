CREATE TABLE workloads (
    workload_id    VARCHAR(32)  NOT NULL,      -- "qlora-sft-v1"  ← Scheduler dockerImageId
    name           VARCHAR(64)  NOT NULL,
    image_digest   CHAR(71)     NOT NULL,      -- "sha256:..."  ← Worker에 실제로 보낼 값
    min_vram_mb    INT          NOT NULL,      -- Scheduler Feasibility 하한 (Backend는 값만 전달)
    default_config JSON         NULL,          -- AUTO 기본값
    limits         JSON         NULL,
    enabled        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     DATETIME(6)  NOT NULL,
    updated_at     DATETIME(6)  NOT NULL,
    PRIMARY KEY (workload_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE base_models (
    model_id     VARCHAR(64)  NOT NULL,        -- "qwen2.5-7b"  ← Scheduler modelId
    workload_id  VARCHAR(32)  NOT NULL,        -- 이미지 1 : 모델 N
    display_name VARCHAR(128) NOT NULL,
    param_count  BIGINT       NULL,
    weights_hash CHAR(66)     NULL,
    source_uri   VARCHAR(512) NULL,
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   DATETIME(6)  NOT NULL,
    updated_at   DATETIME(6)  NOT NULL,
    PRIMARY KEY (model_id),
    CONSTRAINT fk_base_models_workload FOREIGN KEY (workload_id) REFERENCES workloads (workload_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- seed: Scheduler가 학습 때 본 값 1건씩 (완료 기준 §11-2)
INSERT INTO workloads (workload_id, name, image_digest, min_vram_mb, created_at, updated_at)
VALUES ('qlora-sft-v1', 'QLoRA SFT 텍스트 파인튜닝',
        'sha256:0000000000000000000000000000000000000000000000000000000000000000',  -- TODO: 실제 이미지 digest 로 교체 (System 담당)
        8192, NOW(6), NOW(6));

INSERT INTO base_models (model_id, workload_id, display_name, created_at, updated_at)
VALUES ('qwen2.5-7b', 'qlora-sft-v1', 'Qwen 2.5 7B', NOW(6), NOW(6));
