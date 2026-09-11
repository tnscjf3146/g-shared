package com.gshared.back.worker.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum WorkerErrorCode implements ErrorCode {

    WORKER_NOT_FOUND(HttpStatus.NOT_FOUND, "Worker를 찾을 수 없습니다."),
    // 401: 토큰이 없거나 어떤 워커도 가리키지 않는다. 워커는 이 코드를 받으면 재연결을 반복하지 않는다(규격 §4).
    INVALID_WORKER_TOKEN(HttpStatus.UNAUTHORIZED, "유효하지 않은 Worker 토큰입니다."),
    // 403: 토큰은 유효하지만 요청이 가리키는 워커가 그 토큰의 워커가 아니다 — 남의 워커를 건드리려는 것.
    WORKER_FORBIDDEN(HttpStatus.FORBIDDEN, "이 Worker에 대한 권한이 없습니다."),
    INVALID_OPERATOR_ADDRESS(HttpStatus.BAD_REQUEST, "operatorAddress 형식이 올바르지 않습니다. (0x + 16진수 40자)"),
    // 409: 지금은 안 되지만 작업을 끝내거나 취소하면 된다 — 요청 자체가 틀린 것이 아니다.
    WORKER_BUSY(HttpStatus.CONFLICT, "작업 중인 Worker는 등록 해제할 수 없습니다. 작업을 먼저 취소해주세요.");

    private final HttpStatus status;
    private final String message;
}
