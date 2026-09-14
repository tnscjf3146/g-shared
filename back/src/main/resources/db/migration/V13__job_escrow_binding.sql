-- 온체인 예치 연동 (2026-09-14 backend-blockchain-tasks §3·§4·§6).
-- ① provider_wallet: 견적 시점에 확정한 공급자 정산 지갑. 온체인 createAndFundJob 의 provider 와 같아야 하고,
--    예치 확인이 JobFunded.provider 를 이 값과 대조한다. 매칭도 이 지갑의 워커에게만 배정한다.
-- ② escrow_chain_id · escrow_contract_address: 이 Job 이 예치된 체인·컨트랙트. 전역 설정만 쓰면 재배포 뒤
--    예전 Job 을 읽을 수 없다. 예치 확인 시점의 설정값을 박아 둔다.
-- ③ fund_tx_hash: 예치 트랜잭션. 같은 txHash 재전송은 멱등, 다른 Job 에 재사용은 UNIQUE 가 막는다.
--    CREATED 인데 fund_tx_hash 가 있으면 "예치 확인 보류(체인 미확인)" — reconciliation 이 다시 확인한다.
ALTER TABLE jobs
    ADD COLUMN provider_wallet         VARCHAR(42) NULL AFTER recommended_worker_id,
    ADD COLUMN escrow_chain_id         BIGINT      NULL AFTER external_ref,
    ADD COLUMN escrow_contract_address VARCHAR(42) NULL AFTER escrow_chain_id,
    ADD COLUMN fund_tx_hash            CHAR(66)    NULL AFTER escrow_job_id;

ALTER TABLE jobs
    ADD CONSTRAINT uk_jobs_fund_tx_hash UNIQUE (fund_tx_hash),
    -- 온체인 Job 의 유일 식별자 = (chain, contract, jobId). NULL 이 섞인 행(예치 전)은 MySQL 이 중복으로 보지 않는다.
    ADD CONSTRAINT uk_jobs_escrow_job UNIQUE (escrow_chain_id, escrow_contract_address, escrow_job_id),
    -- keeper·reconciliation 순찰이 상태로 훑는다 (owner 무관).
    ADD KEY idx_jobs_status (status);
