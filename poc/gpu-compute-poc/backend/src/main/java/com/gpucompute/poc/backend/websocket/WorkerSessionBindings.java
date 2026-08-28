package com.gpucompute.poc.backend.websocket;

import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

@Component
public class WorkerSessionBindings {

    private final ConcurrentHashMap<String, String> workerIdsBySession = new ConcurrentHashMap<>();

    public boolean bind(String sessionId, String workerId) {
        return workerIdsBySession.putIfAbsent(sessionId, workerId) == null;
    }

    public Optional<String> findWorkerId(String sessionId) {
        return Optional.ofNullable(workerIdsBySession.get(sessionId));
    }

    public Optional<String> remove(String sessionId) {
        return Optional.ofNullable(workerIdsBySession.remove(sessionId));
    }
}
