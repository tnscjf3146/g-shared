import { ApiError } from '@/lib/fetcher'

// 서버 코드 → 문구(job.md §7). INVALID_INPUT 은 서버가 준 필드 메시지 그대로. 토큰 401 은 authFetch 가 처리
const MESSAGES: Record<string, string> = {
  JOB_NOT_FOUND: '작업을 찾을 수 없습니다.',
  INVALID_JOB_STATUS: '현재 상태에서는 취소할 수 없습니다. 목록을 새로 고칩니다.',
}
const FALLBACK = '잠시 후 다시 시도해 주세요.'

export function jobMessageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'INVALID_INPUT') return err.message
    return MESSAGES[err.code] ?? FALLBACK
  }
  return FALLBACK
}
