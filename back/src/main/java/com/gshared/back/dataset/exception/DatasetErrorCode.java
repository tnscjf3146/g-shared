package com.gshared.back.dataset.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum DatasetErrorCode implements ErrorCode {

    DATASET_NOT_FOUND(HttpStatus.NOT_FOUND, "Dataset을 찾을 수 없습니다.");

    private final HttpStatus status;
    private final String message;
}
