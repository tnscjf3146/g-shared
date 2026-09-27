import { ApiError } from '@/lib/fetcher'

// 서버 코드 → 문구 (worker.md §7). 토큰 401 은 authFetch 가 처리
const MESSAGES: Record<string, string> = {
  WORKER_NOT_FOUND: '이미 해제된 워커입니다. 목록을 새로 고칩니다.',
  WORKER_BUSY: '작업 중인 워커는 해제할 수 없습니다. 작업을 먼저 취소해 주세요.',
  WORKER_FORBIDDEN: '내 워커만 해제할 수 있습니다.', // back WorkerErrorCode — FORBIDDEN 으로 적혀 있어 폴백으로 빠졌다(2026-09-15)
  // 작업 이력이 있는 워커는 하드 삭제가 FK 제약에 걸려 여기로 온다(수정요청서 BE-06 · FE-09).
  // 폴백("잠시 후 다시 시도")은 기다리면 된다는 뜻이라 거짓말이다 — 이 워커는 기다려도 지워지지 않는다.
  // 백엔드가 소프트 삭제로 바꾸기 전까지는 왜 안 되는지를 그대로 말한다.
  CONFLICT: '이 워커로 실행한 작업 기록이 있어 지울 수 없습니다. 기록을 지우지 않고 해제하는 기능은 준비 중입니다 — 워커 프로그램을 종료하면 목록에서 오프라인으로 표시됩니다.',
}
const FALLBACK = '잠시 후 다시 시도해 주세요.'

export function workerMessageFor(err: unknown): string {
  return err instanceof ApiError ? (MESSAGES[err.code] ?? FALLBACK) : FALLBACK
}
