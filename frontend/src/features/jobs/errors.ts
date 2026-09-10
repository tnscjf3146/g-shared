import { ApiError } from '@/lib/fetcher'

// 서버 코드 → 문구(job.md §7). INVALID_INPUT 은 서버가 준 필드 메시지 그대로. 토큰 401 은 authFetch 가 처리.
// 409 는 기다리면 되는 상황, 422 는 요청(설정)을 바꿔야 하는 상황 — 문구 방향이 정반대(2026-09-10 백엔드 안내)
const MESSAGES: Record<string, string> = {
  JOB_NOT_FOUND: '작업을 찾을 수 없습니다.',
  INVALID_JOB_STATUS: '현재 상태에서는 취소할 수 없습니다. 목록을 새로 고칩니다.',
  DATASET_NOT_FOUND: '데이터셋 정보를 찾을 수 없습니다. 3단계에서 파일을 다시 올려 주세요.',
  WORKLOAD_NOT_FOUND: '지원하지 않는 워크로드입니다. 1단계에서 다시 선택해 주세요.',
  BASE_MODEL_NOT_FOUND: '이 워크로드에서 쓸 수 없는 모델입니다. 1단계에서 다시 선택해 주세요.',
  INVALID_TRAINING_CONFIG: '학습 설정 4값(epochs·batch size·learning rate·max length)을 확인해 주세요.',
  NO_AVAILABLE_WORKER: '지금 일할 수 있는 GPU 가 없습니다. 설정은 그대로 두고 잠시 후 다시 시도해 주세요.',
  NO_FEASIBLE_GPU: '이 설정으로 실행할 수 있는 GPU 가 없습니다. 모델이나 학습 설정(batch size·max length)을 줄여 주세요.',
  SCHEDULER_REJECTED: '스케줄러가 요청을 거절했습니다. 설정을 확인해 주세요.',
  SCHEDULER_UNAVAILABLE: '스케줄러에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
}
const FALLBACK = '잠시 후 다시 시도해 주세요.'

export function jobMessageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'INVALID_INPUT') return err.message
    return MESSAGES[err.code] ?? FALLBACK
  }
  return FALLBACK
}
