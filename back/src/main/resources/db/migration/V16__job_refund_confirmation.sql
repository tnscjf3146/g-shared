-- FAILED/CANCELLED 만으로 환불을 추정하지 않는다. 기존 행은 체인 조회 후 확인한다.
ALTER TABLE jobs ADD COLUMN refund_confirmed_at DATETIME(6) NULL;
CREATE INDEX idx_jobs_refund_recovery ON jobs (status, refund_confirmed_at);
