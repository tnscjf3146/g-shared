package com.gshared.back.workload.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum WorkloadErrorCode implements ErrorCode {

    WORKLOAD_NOT_FOUND(HttpStatus.NOT_FOUND, "지원하지 않는 Workload입니다."),
    BASE_MODEL_NOT_FOUND(HttpStatus.NOT_FOUND, "지원하지 않는 Base Model입니다.");

    private final HttpStatus status;
    private final String message;
}
