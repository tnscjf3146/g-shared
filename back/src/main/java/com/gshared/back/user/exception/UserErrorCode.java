package com.gshared.back.user.exception;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum UserErrorCode implements ErrorCode {

    USER_NOT_FOUND(HttpStatus.NOT_FOUND, "사용자를 찾을 수 없습니다."),
    DUPLICATE_EMAIL(HttpStatus.CONFLICT, "이미 사용중인 이메일입니다."),

    INVALID_WALLET_ADDRESS(HttpStatus.BAD_REQUEST, "지갑 주소 형식이 올바르지 않습니다."),
    CHAIN_NOT_SUPPORTED(HttpStatus.BAD_REQUEST, "지원하지 않는 네트워크입니다."),
    INVALID_WALLET_SIGNATURE(HttpStatus.UNAUTHORIZED, "지갑 서명이 올바르지 않습니다."),
    WALLET_ALREADY_LINKED(HttpStatus.CONFLICT, "이미 다른 계정에 연결된 지갑입니다."),
    // 만료와 이미 사용된 경우를 Redis 에서 구분해 보관하지 않으므로 하나로 처리한다.
    CHALLENGE_INVALID(HttpStatus.GONE, "만료되었거나 이미 사용된 요청입니다. 다시 시도해주세요.");

    private final HttpStatus status;
    private final String message;
}
