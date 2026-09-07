package com.gshared.back.worker.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum WorkerErrorCode implements ErrorCode {

    WORKER_NOT_FOUND(HttpStatus.NOT_FOUND, "Worker를 찾을 수 없습니다."),
    INVALID_WORKER_TOKEN(HttpStatus.UNAUTHORIZED, "유효하지 않은 Worker 토큰입니다.");

    private final HttpStatus status;
    private final String message;
}
