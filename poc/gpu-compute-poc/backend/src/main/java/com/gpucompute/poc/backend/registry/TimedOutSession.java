package com.gpucompute.poc.backend.registry;

import org.springframework.web.socket.WebSocketSession;
import java.util.UUID;

public record TimedOutSession(
    String workerId,
    WebSocketSession session,
    UUID activeJobId
) {
    public TimedOutSession(String workerId, WebSocketSession session) {
        this(workerId, session, null);
    }
}
