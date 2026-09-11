package com.gshared.back.global.storage;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum StorageErrorCode implements ErrorCode {

    INVALID_OBJECT_KEY(HttpStatus.BAD_REQUEST, "objectKey 형식이 올바르지 않습니다."),
    // 403: 서명이 틀렸거나 만료된 URL — 다시 발급받아야 한다
    INVALID_SIGNATURE(HttpStatus.FORBIDDEN, "서명 URL이 유효하지 않거나 만료되었습니다."),
    OBJECT_NOT_FOUND(HttpStatus.NOT_FOUND, "오브젝트를 찾을 수 없습니다."),
    STORAGE_IO_FAILED(HttpStatus.INTERNAL_SERVER_ERROR, "스토리지 입출력에 실패했습니다.");

    private final HttpStatus status;
    private final String message;
}
