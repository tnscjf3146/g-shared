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
    // 결과물 업로드 완료 검증 — 404: 올라온 파일이 없다 / 422: 파일은 있는데 신고한 크기·해시와 다르다
    RESULT_OBJECT_NOT_FOUND(HttpStatus.NOT_FOUND, "업로드된 결과물을 찾을 수 없습니다."),
    RESULT_UPLOAD_MISMATCH(HttpStatus.UNPROCESSABLE_CONTENT, "업로드된 결과물의 크기 또는 해시가 신고값과 다릅니다."),
    RESULT_OBJECT_KEY_MISMATCH(HttpStatus.BAD_REQUEST, "objectKey가 이 Job의 결과물 경로가 아닙니다.");

    private final HttpStatus status;
    private final String message;
}
