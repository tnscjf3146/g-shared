package com.gshared.back.job.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum JobErrorCode implements ErrorCode {

    JOB_NOT_FOUND(HttpStatus.NOT_FOUND, "Job을 찾을 수 없습니다."),
    INVALID_JOB_STATUS(HttpStatus.CONFLICT, "현재 상태에서 할 수 없는 작업입니다."),
    // 409: 기다리면 해결 (지금 일할 워커가 없을 뿐) — NO_FEASIBLE_GPU(422, 요청을 바꿔야 함)와 구분
    NO_AVAILABLE_WORKER(HttpStatus.CONFLICT, "현재 사용 가능한 워커가 없습니다. 잠시 후 다시 시도해주세요."),
    INVALID_TRAINING_CONFIG(HttpStatus.BAD_REQUEST, "trainingConfig가 올바르지 않습니다. (epochs·batchSize·learningRate·maxLength 필수)"),
    INVALID_ESCROW_JOB_ID(HttpStatus.BAD_REQUEST, "escrowJobId는 0 이상의 정수 문자열이어야 합니다."),
    // 예치 확인 — 422: 영수증은 있는데 이 Job 의 예치가 아니다(되돌려짐·다른 externalRef·다른 provider·다른 가격 등)
    ESCROW_FUNDING_REJECTED(HttpStatus.UNPROCESSABLE_CONTENT, "트랜잭션이 이 작업의 예치로 확인되지 않습니다."),
    // 409: 이 txHash 는 이미 다른 Job 의 예치로 기록됐다
    FUND_TX_ALREADY_USED(HttpStatus.CONFLICT, "이미 다른 작업의 예치로 사용된 트랜잭션입니다."),
    // 503: 운영(fail closed)인데 체인에 물어볼 수 없다 — 프런트는 같은 txHash 로 다시 시도한다
    ESCROW_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "블록체인 노드에 확인할 수 없습니다. 잠시 후 다시 시도해주세요."),
    // 422: 견적에 provider 지갑이 없어 예치 트랜잭션을 만들 수 없다(공급자 지갑 미등록)
    PROVIDER_WALLET_MISSING(HttpStatus.UNPROCESSABLE_CONTENT, "추천된 공급자의 정산 지갑이 등록되어 있지 않습니다."),
    // 결과물 업로드 완료 검증 — 404: 올라온 파일이 없다 / 422: 파일은 있는데 신고한 크기·해시와 다르다
    RESULT_OBJECT_NOT_FOUND(HttpStatus.NOT_FOUND, "업로드된 결과물을 찾을 수 없습니다."),
    RESULT_UPLOAD_MISMATCH(HttpStatus.UNPROCESSABLE_CONTENT, "업로드된 결과물의 크기 또는 해시가 신고값과 다릅니다."),
    RESULT_OBJECT_KEY_MISMATCH(HttpStatus.BAD_REQUEST, "objectKey가 이 Job의 결과물 경로가 아닙니다."),
    // 데이터셋 원본 업로드 — 결과물과 같은 3단계. 409 DATASET_NOT_UPLOADED 는 /fund 가 업로드 전이라 거절한 것
    DATASET_OBJECT_NOT_FOUND(HttpStatus.NOT_FOUND, "업로드된 데이터셋 파일을 찾을 수 없습니다. 먼저 업로드 URL로 파일을 올려주세요."),
    DATASET_UPLOAD_MISMATCH(HttpStatus.UNPROCESSABLE_CONTENT, "업로드된 데이터셋 파일의 크기 또는 해시가 신고값과 다릅니다."),
    DATASET_METADATA_MISMATCH(HttpStatus.UNPROCESSABLE_CONTENT, "업로드된 파일이 등록된 데이터셋 메타데이터(크기·해시)와 다릅니다. 분석한 파일을 그대로 올려주세요."),
    DATASET_OBJECT_KEY_MISMATCH(HttpStatus.BAD_REQUEST, "objectKey가 이 Job의 데이터셋 경로가 아닙니다."),
    DATASET_NOT_UPLOADED(HttpStatus.CONFLICT, "데이터셋 파일이 아직 업로드되지 않았습니다. 업로드를 마친 뒤 예치해주세요."),
    // 입력 어댑터(.tar) 업로드 — 데이터셋과 같은 3단계, 같은 상태코드 체계
    ADAPTER_NOT_EXPECTED(HttpStatus.CONFLICT, "어댑터를 쓰지 않는 작업입니다. 주문 때 hasAdapter를 지정해주세요."),
    ADAPTER_OBJECT_NOT_FOUND(HttpStatus.NOT_FOUND, "업로드된 어댑터 파일을 찾을 수 없습니다. 먼저 업로드 URL로 파일을 올려주세요."),
    ADAPTER_UPLOAD_MISMATCH(HttpStatus.UNPROCESSABLE_CONTENT, "업로드된 어댑터 파일의 크기 또는 해시가 신고값과 다릅니다."),
    ADAPTER_INVALID_FORMAT(HttpStatus.UNPROCESSABLE_CONTENT, "어댑터는 adapter_config.json과 adapter_model.safetensors가 담긴 비압축 .tar 여야 합니다."),
    ADAPTER_TOO_LARGE(HttpStatus.CONTENT_TOO_LARGE, "어댑터 파일은 1 GiB 이하여야 합니다."),
    ADAPTER_OBJECT_KEY_MISMATCH(HttpStatus.BAD_REQUEST, "objectKey가 이 Job의 어댑터 경로가 아닙니다."),
    ADAPTER_NOT_UPLOADED(HttpStatus.CONFLICT, "어댑터 파일이 아직 업로드되지 않았습니다. 업로드를 마친 뒤 예치해주세요."),
    // 이어서 학습 — 내 완료된 학습 작업의 결과물만 다음 학습의 출발점이 될 수 있다
    PARENT_JOB_NOT_FOUND(HttpStatus.NOT_FOUND, "이어서 학습할 작업을 찾을 수 없습니다."),
    PARENT_JOB_NOT_COMPLETED(HttpStatus.CONFLICT, "완료된 학습 작업의 어댑터만 이어서 학습할 수 있습니다."),
    PARENT_ADAPTER_NOT_FOUND(HttpStatus.NOT_FOUND, "그 작업의 어댑터 파일을 찾을 수 없습니다."),
    PARENT_BASE_MODEL_MISMATCH(HttpStatus.CONFLICT, "이어서 학습할 어댑터는 같은 베이스 모델로 학습한 것이어야 합니다."),
    INVALID_ADAPTER_NAME(HttpStatus.BAD_REQUEST, "어댑터 이름은 한글·영문·숫자와 - _ . 공백만 쓸 수 있는 1~60자입니다.");

    private final HttpStatus status;
    private final String message;
}
