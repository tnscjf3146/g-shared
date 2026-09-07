package com.gshared.back.worker.websocket;

import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * "workerId → 현재 열린 WebSocket 연결" 전화번호부.
 *
 * <p>연결(WebSocketSession)은 서버 메모리에만 존재하는 휘발성 객체라 DB에 못 넣는다.
 * 백엔드가 특정 워커에게 JOB_ASSIGN/JOB_CANCEL 을 보낼 때 여기서 연결을 찾는다.
 *
 * <p>여러 워커가 서로 다른 쓰레드에서 동시에 접속/종료하므로 ConcurrentHashMap 사용.
 */
@Component
public class WorkerSessionRegistry {

    private final ConcurrentHashMap<String, WebSocketSession> sessions = new ConcurrentHashMap<>();

    /**
     * 연결 등록(REGISTER 성공 시). 같은 워커가 재접속하면 이전 연결을 덮어쓴다.
     * @return 덮어써진 이전 연결(있으면). 호출자가 닫아줘야 한다.
     */
    public WebSocketSession add(String workerId, WebSocketSession session) {
        return sessions.put(workerId, session);
    }

    /**
     * 연결 제거(연결 종료 시). "지금 표에 적힌 연결이 바로 이 세션일 때만" 지운다.
     * 재연결 직후 옛 세션의 종료 이벤트가 늦게 도착해도 새 연결을 지우지 않게 하는 원자 연산.
     * @return 실제로 지웠으면 true (= 이 세션이 마지막 유효 연결이었음)
     */
    public boolean remove(String workerId, WebSocketSession session) {
        return sessions.remove(workerId, session);
    }

    /** 특정 워커의 현재 연결 조회. 없으면(오프라인) empty. */
    public Optional<WebSocketSession> find(String workerId) {
        return Optional.ofNullable(sessions.get(workerId));
    }

    /** 현재 연결된 워커 수(모니터링용). */
    public int size() {
        return sessions.size();
    }
}
