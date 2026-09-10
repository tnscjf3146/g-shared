package com.gshared.back.worker.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum WorkerErrorCode implements ErrorCode {

    WORKER_NOT_FOUND(HttpStatus.NOT_FOUND, "Worker를 찾을 수 없습니다."),
    INVALID_WORKER_TOKEN(HttpStatus.UNAUTHORIZED, "유효하지 않은 Worker 토큰입니다."),
    // 409: 지금은 안 되지만 작업을 끝내거나 취소하면 된다 — 요청 자체가 틀린 것이 아니다.
    WORKER_BUSY(HttpStatus.CONFLICT, "작업 중인 Worker는 등록 해제할 수 없습니다. 작업을 먼저 취소해주세요.");

    private final HttpStatus status;
    private final String message;
}
