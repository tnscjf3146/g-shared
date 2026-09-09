package com.gshared.back.global.client;

import com.gshared.back.global.exception.ErrorCode;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum SchedulerErrorCode implements ErrorCode {

    // 422: 요청을 바꿔야 해결되는 것 (기다려도 소용없음)
    SCHEDULER_REJECTED(HttpStatus.UNPROCESSABLE_ENTITY, "Scheduler가 요청을 거절했습니다."),
    NO_FEASIBLE_GPU(HttpStatus.UNPROCESSABLE_ENTITY, "이 설정으로 실행 가능한 GPU가 없습니다."),

    // 503: 기다리면 해결될 수 있는 것 (모델 미로드·타임아웃·연결 불가)
    SCHEDULER_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Scheduler에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.");

    private final HttpStatus status;
    private final String message;
}
