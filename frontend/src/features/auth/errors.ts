import { ApiError } from '@/lib/fetcher'

// 서버 에러 코드 → 화면 문구 (auth.md §6). INVALID_INPUT 만 서버가 준 필드 메시지를 그대로 쓴다
const MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: '이메일 또는 비밀번호가 올바르지 않습니다.',
  DUPLICATE_EMAIL: '이미 사용 중인 이메일입니다.',
  // 같은 이메일로 동시에 가입하면 DB unique 위반이 DUPLICATE_EMAIL 로 번역되지 못하고 이 코드로 나간다
  // (수정요청서 BE-23 · FE-09). 백엔드가 변환해 주기 전까지는 실제 원인인 중복과 같은 말을 해 준다 —
  // 폴백("잠시 후 다시 시도")은 다시 눌러도 같은 결과라 사용자를 헛돌게 만든다
  CONFLICT: '이미 사용 중인 이메일입니다.',
  TOO_MANY_REQUESTS: '시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.',
  EXPIRED_TOKEN: '다시 로그인해 주세요.',
  INVALID_TOKEN: '다시 로그인해 주세요.',
  UNAUTHORIZED: '다시 로그인해 주세요.',
}
const FALLBACK = '잠시 후 다시 시도해 주세요.'

/** 토큰이 무효해 재발급을 시도해 볼 만한 401 코드 */
export const RETRYABLE_401 = new Set(['EXPIRED_TOKEN', 'INVALID_TOKEN', 'UNAUTHORIZED'])

export function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'INVALID_INPUT') return err.message
    return MESSAGES[err.code] ?? FALLBACK
  }
  return FALLBACK
}
