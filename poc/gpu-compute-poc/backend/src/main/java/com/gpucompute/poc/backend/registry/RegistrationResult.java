package com.gpucompute.poc.backend.registry;

import java.util.Optional;

import org.springframework.web.socket.WebSocketSession;

public record RegistrationResult(
    WorkerRecord current,
    Optional<WebSocketSession> replacedSession
) {
}
