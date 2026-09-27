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
  NO_AVAILABLE_WORKER: '현재 작업 가능한 GPU가 없습니다. 설정은 그대로 두고 잠시 후 다시 시도해 주세요.',
  // 고른 등급에 지금 돌릴 수 있는 워커가 없다 — 목록을 본 뒤 누르기까지 사이에 그 GPU 가 일을 잡았거나
  // 설정이 바뀌어 후보에서 빠진 경우. 설정을 바꿀 필요는 없고 다른 GPU 를 고르거나 기다리면 된다.
  SELECTED_WORKER_UNAVAILABLE: '선택하신 GPU를 지금 쓸 수 없습니다. 4단계에서 다른 GPU를 고르거나 잠시 후 다시 시도해 주세요.',
  NO_FEASIBLE_GPU: '이 설정으로 실행할 수 있는 GPU 가 없습니다. 모델이나 학습 설정(batch size·max length)을 줄여 주세요.',
  // "스케줄러" 는 우리 내부 부품 이름이다 — 사용자가 읽고 무엇을 해야 할지 알 수 없다.
  // 뜻은 그대로 두되(422=설정을 바꿔야 함 / 503=기다리면 됨) 화면에는 GPU 추천이라는 일로 말한다.
  SCHEDULER_REJECTED: '이 설정으로는 GPU 추천을 계산할 수 없습니다. 학습 설정(batch size·max length)을 줄여 주세요.',
  SCHEDULER_UNAVAILABLE: '지금 GPU 추천을 계산할 수 없습니다. 설정은 그대로 두고 잠시 후 다시 시도해 주세요.',
  // 데이터셋 원본 업로드(back JobErrorCode, 2026-09-15). 상세 화면에는 재업로드가 없다 — 취소 후 다시 등록이 유일한 복구
  DATASET_NOT_UPLOADED: '데이터셋 파일 업로드가 끝나지 않은 작업입니다. 이 작업을 취소하고 워크로드 등록을 다시 진행해 주세요.',
  DATASET_OBJECT_NOT_FOUND: '업로드된 데이터셋 파일을 찾을 수 없습니다. 업로드를 다시 진행해 주세요.',
  DATASET_UPLOAD_MISMATCH: '업로드된 파일이 분석한 파일과 다릅니다(크기·해시 불일치). 분석한 파일을 그대로 올려 주세요.',
  DATASET_METADATA_MISMATCH: '업로드된 파일이 분석한 파일과 다릅니다(크기·해시 불일치). 분석한 파일을 그대로 올려 주세요.',
  DATASET_OBJECT_KEY_MISMATCH: '업로드 경로가 이 작업의 것이 아닙니다. 업로드를 처음부터 다시 진행해 주세요.',
  // 어댑터 버전·이름(job.md §11). 이어서 학습은 "완료된 내 학습 작업" 만 출발점이 될 수 있다
  PARENT_JOB_NOT_FOUND: '이어서 학습할 작업을 찾을 수 없습니다. 2단계에서 어댑터를 다시 선택해 주세요.',
  PARENT_JOB_NOT_COMPLETED: '아직 학습이 끝나지 않은 작업입니다. 완료된 학습 작업의 어댑터만 이어서 학습할 수 있습니다.',
  PARENT_ADAPTER_NOT_FOUND: '그 작업의 어댑터 파일을 찾을 수 없습니다. 다른 어댑터를 선택해 주세요.',
  PARENT_BASE_MODEL_MISMATCH: '이어서 학습할 어댑터는 같은 베이스 모델로 학습한 것이어야 합니다. 2단계에서 모델이나 어댑터를 다시 선택해 주세요.',
  INVALID_ADAPTER_NAME: '어댑터 이름을 확인해 주세요. 한글·영문·숫자와 - _ . 공백만 쓸 수 있습니다.',
  // 입력 어댑터 .tar 업로드(lora-adapter-frontend-작업 §2). 상세에는 재업로드가 없다 — 취소 후 다시 등록이 유일한 복구
  ADAPTER_NOT_UPLOADED: '어댑터 파일 업로드가 끝나지 않은 작업입니다. 이 작업을 취소하고 워크로드 등록을 다시 진행해 주세요.',
  ADAPTER_UPLOAD_MISMATCH: '업로드된 어댑터가 선택한 파일과 다릅니다(크기·해시 불일치). 같은 파일을 그대로 다시 올려 주세요.',
  ADAPTER_INVALID_FORMAT: '어댑터 .tar 에 adapter_config.json · adapter_model.safetensors 가 있어야 합니다.',
  ADAPTER_TOO_LARGE: '어댑터는 1 GiB 이하여야 합니다.',
  ADAPTER_OBJECT_NOT_FOUND: '업로드된 어댑터 파일을 찾을 수 없습니다. 업로드를 다시 진행해 주세요.',
  ADAPTER_NOT_EXPECTED: '이 작업은 어댑터를 쓰지 않도록 등록됐습니다. 워크로드 등록을 다시 진행해 주세요.',
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
