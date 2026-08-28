package com.gpucompute.poc.backend.protocol;

import org.springframework.web.socket.CloseStatus;

public class ProtocolException extends RuntimeException {

    private final CloseStatus closeStatus;

    public ProtocolException(CloseStatus closeStatus, String reason) {
        super(reason);
        this.closeStatus = closeStatus;
    }

    public ProtocolException(CloseStatus closeStatus, String reason, Throwable cause) {
        super(reason, cause);
        this.closeStatus = closeStatus;
    }

    public CloseStatus closeStatus() {
        return closeStatus;
    }
}
