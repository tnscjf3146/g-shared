package com.gshared.back.worker.dto.response;

/**
 * 등록 응답 (규격 §3). Worker 클라이언트는 workerId·workerToken(원문)을 DPAPI 로 보호해 두었다가
 * 이후 인증·WebSocket 연결·워커 전용 REST 호출에 쓴다.
 * 토큰 원문은 이 응답에서만 노출된다 (DB엔 해시만 저장).
 * operatorAddress 는 서버가 정규화(소문자)해 저장한 값을 되돌려 준다.
 */
public record RegisterWorkerResponse(
        String workerId,
        String workerToken,
        String operatorAddress
) {
}
