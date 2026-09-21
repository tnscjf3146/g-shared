-- 취소 사유 코드 (2026-09-21) — "누가 취소를 유발했나"를 저장해 화면이 정직하게 말할 수 있게.
-- USER_REQUEST(사용자) / PROVIDER_TIMEOUT(공급자 무응답·중단, 키퍼 환불) / SYSTEM_RECONCILED(시스템 정리).
-- 학습 자체 실패는 기존 fail_reason 이 담당한다 — 두 칸의 역할이 다르다(원인 코드 vs 에러 상세).
ALTER TABLE jobs ADD COLUMN cancel_reason VARCHAR(32) NULL;
