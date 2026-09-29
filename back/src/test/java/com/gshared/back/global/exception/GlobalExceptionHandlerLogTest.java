package com.gshared.back.global.exception;

import com.gshared.back.global.client.SchedulerErrorCode;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.mock.web.MockHttpServletRequest;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 비즈니스 예외 로그에 요청 경로가 남는지.
 *
 * <p>같은 NO_FEASIBLE_GPU 라도 견적과 주문은 뜻이 다르다. 경로가 없던 때에는 운영 로그 23줄이
 * 몇 건의 요청이었는지, 그중 주문이 몇 건이었는지 가를 수 없었다(infra/observability/docs/2026-09-21-첫-검증과-발견.md).
 */
@ExtendWith(OutputCaptureExtension.class)
class GlobalExceptionHandlerLogTest {

    private final GlobalExceptionHandler handler = new GlobalExceptionHandler();

    @Test
    @DisplayName("견적과 주문의 NO_FEASIBLE_GPU 가 로그에서 경로로 갈린다")
    void business_exception_log_carries_request_path(CapturedOutput output) {
        handler.handleBusinessException(new BusinessException(SchedulerErrorCode.NO_FEASIBLE_GPU),
                new MockHttpServletRequest("POST", "/api/jobs/estimate"));
        handler.handleBusinessException(new BusinessException(SchedulerErrorCode.NO_FEASIBLE_GPU),
                new MockHttpServletRequest("POST", "/api/jobs"));

        assertThat(output.getOut())
                .contains("비즈니스 예외: NO_FEASIBLE_GPU - 이 설정으로 실행 가능한 GPU가 없습니다. [POST /api/jobs/estimate]")
                .contains("비즈니스 예외: NO_FEASIBLE_GPU - 이 설정으로 실행 가능한 GPU가 없습니다. [POST /api/jobs]");
    }

    @Test
    @DisplayName("쿼리스트링은 로그에 남기지 않는다 — 서명 URL 값이 새지 않게")
    void query_string_is_not_logged(CapturedOutput output) {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/storage/results/1");
        request.setQueryString("expires=1&sig=SECRET");

        handler.handleBusinessException(new BusinessException(SchedulerErrorCode.SCHEDULER_REJECTED), request);

        assertThat(output.getOut())
                .contains("[GET /api/storage/results/1]")
                .doesNotContain("SECRET");
    }
}
