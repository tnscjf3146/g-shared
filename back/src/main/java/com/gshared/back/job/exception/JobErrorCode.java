package com.gshared.back.job.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum JobErrorCode implements ErrorCode {

    JOB_NOT_FOUND(HttpStatus.NOT_FOUND, "Job을 찾을 수 없습니다."),
    INVALID_JOB_STATUS(HttpStatus.CONFLICT, "현재 상태에서 할 수 없는 작업입니다.");

    private final HttpStatus status;
    private final String message;
}
