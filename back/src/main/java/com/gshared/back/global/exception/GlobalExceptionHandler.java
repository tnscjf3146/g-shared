package com.gshared.back.global.exception;

import com.gshared.back.auth.exception.AuthErrorCode;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.validation.FieldError;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<ErrorResponse> handleBusinessException(BusinessException e) {
        ErrorCode errorCode = e.getErrorCode();
        log.warn("비즈니스 예외: {} - {}", errorCode.name(), errorCode.getMessage());

        return toResponse(errorCode);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> handleValidationException(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(FieldError::getDefaultMessage)
                .orElse(CommonErrorCode.INVALID_INPUT.getMessage());
        log.warn("검증 실패: {}", message);

        return toResponse(CommonErrorCode.INVALID_INPUT, ErrorResponse.of(CommonErrorCode.INVALID_INPUT, message));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ErrorResponse> handleNotReadableException(HttpMessageNotReadableException e) {
        log.warn("요청 본문 파싱 실패: {}", e.getMessage());

        return toResponse(CommonErrorCode.INVALID_REQUEST_BODY);
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ErrorResponse> handleAccessDeniedException(AccessDeniedException e) {
        log.warn("접근 거부: {}", e.getMessage());

        return toResponse(AuthErrorCode.ACCESS_DENIED);
    }

    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<ErrorResponse> handleNotFound(NoResourceFoundException e) {
        log.warn("존재하지 않는 경로: {} {}", e.getHttpMethod(), e.getResourcePath());

        return toResponse(CommonErrorCode.NOT_FOUND);
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ErrorResponse> handleMethodNotAllowed(HttpRequestMethodNotSupportedException e) {
        log.warn("지원하지 않는 메서드: {} (지원: {})", e.getMethod(), e.getSupportedHttpMethods());

        return toResponse(CommonErrorCode.METHOD_NOT_ALLOWED);
    }

    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    public ResponseEntity<ErrorResponse> handleUnsupportedMediaType(HttpMediaTypeNotSupportedException e) {
        log.warn("지원하지 않는 Content-Type: {}", e.getContentType());

        return toResponse(CommonErrorCode.UNSUPPORTED_MEDIA_TYPE);
    }

    /**
     * 필수 쿼리 파라미터 누락. 클라이언트 잘못이므로 400 이다.
     *
     * <p>따로 잡지 않으면 맨 아래 catch-all 로 떨어져 500 이 나간다 — 서명 없이
     * {@code /api/storage/**} 를 열어 본 요청이 "서버 오류"로 보이고, 로그에도
     * "예상하지 못한 예외"로 스택이 통째로 남는다. 서명 URL 창구는 바깥에서
     * 닿는 주소라 이런 요청이 정상적으로 들어온다.
     */
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<ErrorResponse> handleMissingParameter(MissingServletRequestParameterException e) {
        log.warn("필수 파라미터 누락: {}", e.getParameterName());

        return toResponse(CommonErrorCode.INVALID_INPUT);
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ErrorResponse> handleTypeMismatch(MethodArgumentTypeMismatchException e) {
        log.warn("파라미터 타입 불일치: {} = {}", e.getName(), e.getValue());

        return toResponse(CommonErrorCode.INVALID_INPUT);
    }

    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ErrorResponse> handleDataIntegrityViolation(DataIntegrityViolationException e) {
        log.warn("데이터 무결성 위반: {}", e.getMostSpecificCause().getMessage());

        return toResponse(CommonErrorCode.CONFLICT);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> handleException(Exception e) {
        log.error("예상하지 못한 예외", e);

        return toResponse(CommonErrorCode.INTERNAL_SERVER_ERROR);
    }

    private ResponseEntity<ErrorResponse> toResponse(ErrorCode errorCode) {
        return toResponse(errorCode, ErrorResponse.of(errorCode));
    }

    private ResponseEntity<ErrorResponse> toResponse(ErrorCode errorCode, ErrorResponse body) {
        return ResponseEntity.status(errorCode.getStatus()).body(body);
    }
}
