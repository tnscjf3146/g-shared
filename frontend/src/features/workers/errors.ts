import { ApiError } from '@/lib/fetcher'

// 서버 코드 → 문구 (worker.md §7). 토큰 401 은 authFetch 가 처리
const MESSAGES: Record<string, string> = {
  WORKER_NOT_FOUND: '이미 해제된 워커입니다. 목록을 새로 고칩니다.',
  WORKER_BUSY: '작업 중인 워커는 해제할 수 없습니다. 작업을 먼저 취소해 주세요.',
  FORBIDDEN: '내 워커만 해제할 수 있습니다.',
}
const FALLBACK = '잠시 후 다시 시도해 주세요.'

export function workerMessageFor(err: unknown): string {
  return err instanceof ApiError ? (MESSAGES[err.code] ?? FALLBACK) : FALLBACK
}
