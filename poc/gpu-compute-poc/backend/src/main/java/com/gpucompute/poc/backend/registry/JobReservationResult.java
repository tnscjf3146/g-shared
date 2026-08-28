package com.gpucompute.poc.backend.registry;

import java.util.Optional;
import java.util.UUID;

import org.springframework.web.socket.WebSocketSession;

public record JobReservationResult(Status status, WebSocketSession session) {
    public enum Status {
        RESERVED,
        UNKNOWN_WORKER,
        OFFLINE,
        BUSY,
        CUDA_UNAVAILABLE,
        UNSUPPORTED_WORKLOAD
    }

    public static JobReservationResult rejected(Status status) {
        return new JobReservationResult(status, null);
    }

    public Optional<WebSocketSession> activeSession() {
        return Optional.ofNullable(session);
    }
}
