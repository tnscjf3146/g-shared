-- 카탈로그 PK 를 문자열 자연키 → BIGINT 대리키로 전환.
-- AI Scheduler recommend API 가 modelId·dockerImageId 를 정수 PK 로 받도록 변경됨
-- (ai 615f5f3, feature schema v2). 문자열 ID 는 slug 컬럼으로 보존한다 —
-- 이름이 바뀌어도 FK 가 흔들리지 않고, Scheduler 카테고리 feature 도 안정된다.
--
-- 기존 jobs 행은 개발 검증용 데이터뿐이므로 폐기한다 (V7 과 동일 근거 —
-- 문자열 workload_id → 정수 전환과 양립 불가).
UPDATE workers SET current_job_id = NULL;
DELETE FROM jobs;

-- ① 카탈로그를 참조하는 FK 해제 후 테이블 재생성 (seed 전용이라 데이터 이관 불필요)
ALTER TABLE jobs
    DROP FOREIGN KEY fk_jobs_workload,
    DROP FOREIGN KEY fk_jobs_base_model;
DROP TABLE base_models;
DROP TABLE workloads;

CREATE TABLE workloads (
    workload_id    BIGINT       NOT NULL AUTO_INCREMENT,  -- ← Scheduler dockerImageId (정수 PK)
    slug           VARCHAR(32)  NOT NULL,                 -- "qlora-sft-v1" (사람용 식별자)
    name           VARCHAR(64)  NOT NULL,
    image_digest   CHAR(71)     NOT NULL,                 -- "sha256:..."  ← Worker에 실제로 보낼 값
    min_vram_mb    INT          NOT NULL,                 -- Scheduler Feasibility 하한 (Backend는 값만 전달)
    default_config JSON         NULL,                     -- AUTO 기본값
    limits         JSON         NULL,
    enabled        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     DATETIME(6)  NOT NULL,
    updated_at     DATETIME(6)  NOT NULL,
    PRIMARY KEY (workload_id),
    UNIQUE KEY uk_workloads_slug (slug)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

CREATE TABLE base_models (
    model_id     BIGINT       NOT NULL AUTO_INCREMENT,    -- ← Scheduler modelId (정수 PK)
    slug         VARCHAR(64)  NOT NULL,                   -- "qwen2.5-7b"
    workload_id  BIGINT       NOT NULL,                   -- 이미지 1 : 모델 N
    display_name VARCHAR(128) NOT NULL,
    param_count  BIGINT       NULL,
    weights_hash CHAR(66)     NULL,
    source_uri   VARCHAR(512) NULL,
    enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   DATETIME(6)  NOT NULL,
    updated_at   DATETIME(6)  NOT NULL,
    PRIMARY KEY (model_id),
    UNIQUE KEY uk_base_models_slug (slug),
    CONSTRAINT fk_base_models_workload FOREIGN KEY (workload_id) REFERENCES workloads (workload_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- ② seed 재삽입 (V6 과 동일 값 — Scheduler가 학습 때 본 카탈로그 1건씩)
INSERT INTO workloads (slug, name, image_digest, min_vram_mb, created_at, updated_at)
VALUES ('qlora-sft-v1', 'QLoRA SFT 텍스트 파인튜닝',
        'sha256:0000000000000000000000000000000000000000000000000000000000000000',  -- TODO: 실제 이미지 digest 로 교체 (System 담당)
        8192, NOW(6), NOW(6));

INSERT INTO base_models (slug, workload_id, display_name, created_at, updated_at)
SELECT 'qwen2.5-7b', workload_id, 'Qwen 2.5 7B', NOW(6), NOW(6)
FROM workloads WHERE slug = 'qlora-sft-v1';

-- ③ jobs 참조 컬럼 타입 정합 + FK 재연결
ALTER TABLE jobs
    MODIFY COLUMN workload_id   BIGINT NOT NULL,
    MODIFY COLUMN base_model_id BIGINT NOT NULL;
ALTER TABLE jobs
    ADD CONSTRAINT fk_jobs_workload   FOREIGN KEY (workload_id)   REFERENCES workloads (workload_id),
    ADD CONSTRAINT fk_jobs_base_model FOREIGN KEY (base_model_id) REFERENCES base_models (model_id);
