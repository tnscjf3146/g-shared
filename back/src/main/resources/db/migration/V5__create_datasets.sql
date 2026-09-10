CREATE TABLE datasets (
    dataset_id        BIGINT       NOT NULL AUTO_INCREMENT,
    owner_id          BIGINT       NOT NULL,

    -- Scheduler 입력 6개 (하나라도 없으면 예측 불가 → 전부 NOT NULL)
    size_bytes        BIGINT       NOT NULL,   -- datasetSizeBytes
    sample_count      INT          NOT NULL,   -- sampleCount
    total_tokens      BIGINT       NOT NULL,   -- totalTokens     : 데이터셋에 담긴 토큰
    effective_tokens  BIGINT       NOT NULL,   -- effectiveTokens : GPU가 실제 계산하는 토큰 (billing 기준)
    avg_seq_length    DOUBLE       NOT NULL,   -- avgSeqLength    : 평균이라 실수
    max_seq_length    INT          NOT NULL,   -- maxSeqLength

    data_hash         CHAR(66)     NULL,
    storage_uri       VARCHAR(512) NULL,
    uploaded_at       DATETIME(6)  NULL,
    expires_at        DATETIME(6)  NULL,
    created_at        DATETIME(6)  NOT NULL,
    updated_at        DATETIME(6)  NOT NULL,

    PRIMARY KEY (dataset_id),
    CONSTRAINT fk_datasets_owner FOREIGN KEY (owner_id) REFERENCES users (user_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci;
