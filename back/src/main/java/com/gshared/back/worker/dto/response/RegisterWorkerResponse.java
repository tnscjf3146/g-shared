package com.gshared.back.worker.dto.response;

/**
 * 등록 응답. Worker 클라이언트는 이 workerToken(원문)을 저장해두었다가
 * WebSocket 연결(/ws/workers) 시 Authorization: Bearer 로 사용한다.
 * 원문은 이 응답에서만 노출된다 (DB엔 해시만 저장).
 */
public record RegisterWorkerResponse(
        String workerId,
        String workerToken
) {
}
