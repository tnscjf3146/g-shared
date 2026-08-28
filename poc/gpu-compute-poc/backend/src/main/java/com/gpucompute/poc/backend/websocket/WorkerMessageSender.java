package com.gpucompute.poc.backend.websocket;

import java.io.IOException;

import org.springframework.stereotype.Component;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import tools.jackson.databind.ObjectMapper;

import com.gpucompute.poc.backend.protocol.ExecuteMessage;

@Component
public class WorkerMessageSender {
    private final ObjectMapper objectMapper;

    public WorkerMessageSender(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public void send(WebSocketSession session, ExecuteMessage message) throws IOException {
        synchronized (session) {
            session.sendMessage(new TextMessage(objectMapper.writeValueAsString(message)));
        }
    }
}
