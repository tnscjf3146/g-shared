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
  // 데이터셋 원본 업로드(back JobErrorCode, 2026-09-15). 상세 화면에는 재업로드가 없다 — 취소 후 다시 등록이 유일한 복구
  DATASET_NOT_UPLOADED: '데이터셋 파일 업로드가 끝나지 않은 작업입니다. 이 작업을 취소하고 워크로드 등록을 다시 진행해 주세요.',
  DATASET_OBJECT_NOT_FOUND: '업로드된 데이터셋 파일을 찾을 수 없습니다. 업로드를 다시 진행해 주세요.',
  DATASET_UPLOAD_MISMATCH: '업로드된 파일이 분석한 파일과 다릅니다(크기·해시 불일치). 분석한 파일을 그대로 올려 주세요.',
  DATASET_METADATA_MISMATCH: '업로드된 파일이 분석한 파일과 다릅니다(크기·해시 불일치). 분석한 파일을 그대로 올려 주세요.',
  DATASET_OBJECT_KEY_MISMATCH: '업로드 경로가 이 작업의 것이 아닙니다. 업로드를 처음부터 다시 진행해 주세요.',
  // 예치 확인(POST /jobs/{id}/fund). 아래 넷은 "확정 거절" — 같은 txHash 로 다시 보내도 같은 답이라 재시도 안내를 하면 안 된다
  PROVIDER_WALLET_MISSING: '추천된 공급자의 정산 지갑이 등록되어 있지 않아 예치할 수 없습니다. 잠시 후 다시 시도하거나 다른 GPU 등급을 선택해 주세요.',
  ESCROW_FUNDING_REJECTED: '이 트랜잭션은 이 작업의 예치로 확인되지 않습니다. 금액·지갑·작업 참조값이 다릅니다.',
  FUND_TX_ALREADY_USED: '이미 다른 작업의 예치로 사용된 트랜잭션입니다.',
  INVALID_ESCROW_JOB_ID: '온체인 작업 번호가 올바르지 않습니다.',
  ESCROW_UNAVAILABLE: '블록체인 노드에 확인할 수 없습니다. 트랜잭션을 다시 보내지 말고 잠시 후 서버 확인만 다시 해 주세요.',
}

/** 예치 서버 확인이 "나중에 다시 물어보면 될" 실패인가 — 노드 장애(503)·429·5xx·네트워크 단절. 그 외 4xx 는 확정 거절 */
export function isTransientFundError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true // fetch 자체가 실패(오프라인·타임아웃) — 서버 상태를 모른다
  return err.code === 'ESCROW_UNAVAILABLE' || err.status === 429 || err.status >= 500
}
const FALLBACK = '잠시 후 다시 시도해 주세요.'

export function jobMessageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'INVALID_INPUT') return err.message
    return MESSAGES[err.code] ?? FALLBACK
  }
  return FALLBACK
}
