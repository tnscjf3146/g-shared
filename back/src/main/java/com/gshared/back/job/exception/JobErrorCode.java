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
    INVALID_TRAINING_CONFIG(HttpStatus.BAD_REQUEST, "trainingConfig가 올바르지 않습니다. (epochs·batchSize·learningRate·maxLength 필수)");

    private final HttpStatus status;
    private final String message;
}
