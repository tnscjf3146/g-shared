-- 학습 결과물(어댑터)의 이름·버전·계보 (frontend job.md §11).
--
-- 사용자는 한 번 학습한 어댑터에서 "이어서 학습"할 수 있고, 그때 버전이 하나 올라간다(v1 → v2 → v3).
-- 계보는 parent_job_id 로만 잇는다 — 이름은 언제든 바꿀 수 있어 계보의 근거가 될 수 없다.
--
-- adapter_name    : 내려받을 파일 이름이 된다. 주문 때 정하고 나중에 PATCH 로 바꿀 수 있다.
--                   한글이 들어가므로 길이는 문자 수 기준(utf8mb4). 서버가 기본값을 채우므로 NULL 은 옛 행뿐이다.
-- adapter_version : 내 계보에서 몇 번째인가. 처음부터 학습·외부 파일 업로드는 새 계보라 1,
--                   이어서 학습이면 부모 + 1. 서버가 정하며 클라이언트 값은 믿지 않는다.
-- parent_job_id   : 이어서 학습한 원본 Job. 그 Job 의 결과물(result.tar)이 이번 학습의 입력 어댑터다.
--                   FK 를 걸지 않는다 — 부모가 지워져도 이 Job 의 기록(어느 작업에서 이어졌는가)은 남아야 한다.
ALTER TABLE jobs
    ADD COLUMN adapter_name    VARCHAR(60) NULL AFTER adapter_deleted_at,
    ADD COLUMN adapter_version INT         NOT NULL DEFAULT 1 AFTER adapter_name,
    ADD COLUMN parent_job_id   BIGINT      NULL AFTER adapter_version;

-- "이 어댑터에서 이어받은 작업들" 조회용 (계보 표시·삭제 영향 확인)
CREATE INDEX idx_jobs_adapter_parent ON jobs (parent_job_id);
