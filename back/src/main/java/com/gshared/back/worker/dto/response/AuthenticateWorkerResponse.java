package com.gshared.back.worker.dto.response;

/**
 * 워커 재접속 확인 응답. 서버가 인정한 워커의 식별자를 그대로 돌려준다.
 * 워커 클라이언트는 자기가 보낸 값과 이 값을 다시 대조한다.
 */
public record AuthenticateWorkerResponse(String workerId) {
}
