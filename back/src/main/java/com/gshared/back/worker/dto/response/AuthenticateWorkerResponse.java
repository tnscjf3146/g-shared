package com.gshared.back.worker.dto.response;

/**
 * 워커 재접속 확인 응답 (규격 §4). 서버가 인정한 workerId 와 인증 성공 여부.
 * 실패는 200 이 아니라 401/403 으로 나가므로 authenticated 는 항상 true 다 —
 * 규격이 이 필드를 요구해 남겨 둔다.
 */
public record AuthenticateWorkerResponse(
        String workerId,
        boolean authenticated
) {
}
