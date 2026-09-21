-- 취소 사유 코드 (2026-09-21) — "누가 취소를 유발했나"를 저장해 화면이 정직하게 말할 수 있게.
-- USER_REQUEST(사용자) / PROVIDER_TIMEOUT(공급자 무응답·중단, 키퍼 환불) / SYSTEM_RECONCILED(시스템 정리).
-- 학습 자체 실패는 기존 fail_reason 이 담당한다 — 두 칸의 역할이 다르다(원인 코드 vs 에러 상세).
-- 과거(V22 이전) CANCELLED 행은 소급하지 않는다(NULL 유지 → 화면은 기존 '취소됨') — 원인을 사후 추정하면
-- 사용자 취소를 '공급자 무응답 실패'로 오표시할 수 있어, 모르는 과거는 조용한 기본값이 안전하다.
ALTER TABLE jobs ADD COLUMN cancel_reason VARCHAR(32) NULL;
