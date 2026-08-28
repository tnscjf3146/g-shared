package com.gpucompute.poc.backend.websocket;

import java.io.IOException;
import java.time.Clock;

import org.springframework.beans.factory.annotation.Autowired;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerHeartbeat;
import com.gpucompute.poc.backend.protocol.HeartbeatMessage;
import com.gpucompute.poc.backend.protocol.ProtocolException;
import com.gpucompute.poc.backend.protocol.RegisterMessage;
import com.gpucompute.poc.backend.protocol.WorkerMessage;
import com.gpucompute.poc.backend.protocol.WorkerMessageDecoder;
import com.gpucompute.poc.backend.protocol.WorkerMessageValidator;
import com.gpucompute.poc.backend.protocol.ExecuteResultMessage;
import com.gpucompute.poc.backend.protocol.ExecuteStartedMessage;
import com.gpucompute.poc.backend.job.JobService;
import com.gpucompute.poc.backend.registry.JobCompletionResult;
import com.gpucompute.poc.backend.registry.JobProgressResult;
import com.gpucompute.poc.backend.registry.WorkerRegistry;
import com.gpucompute.poc.backend.registry.HeartbeatResult;
import com.gpucompute.poc.backend.registry.RegistrationResult;
import com.gpucompute.poc.backend.registry.WorkerRecord;
import com.gpucompute.poc.backend.config.JobSecurityConfig;

@Component
public class WorkerWebSocketHandler extends TextWebSocketHandler {

    private static final Logger log = LoggerFactory.getLogger(WorkerWebSocketHandler.class);
    private static final CloseStatus REPLACED_BY_NEWER_CONNECTION =
        CloseStatus.POLICY_VIOLATION.withReason("replaced by newer connection");

    private final WorkerRegistry workerRegistry;
    private final WorkerMessageDecoder messageDecoder;
    private final WorkerMessageValidator messageValidator;
    private final WorkerSessionBindings sessionBindings;
    private final Clock clock;
    private final JobService jobService;

    @Autowired
    public WorkerWebSocketHandler(
        WorkerRegistry workerRegistry,
        WorkerMessageDecoder messageDecoder,
        WorkerMessageValidator messageValidator,
        WorkerSessionBindings sessionBindings,
        Clock clock,
        JobService jobService
    ) {
        this.workerRegistry = workerRegistry;
        this.messageDecoder = messageDecoder;
        this.messageValidator = messageValidator;
        this.sessionBindings = sessionBindings;
        this.clock = clock;
        this.jobService = jobService;
    }

    public WorkerWebSocketHandler(
        WorkerRegistry workerRegistry,
        WorkerMessageDecoder messageDecoder,
        WorkerMessageValidator messageValidator,
        WorkerSessionBindings sessionBindings,
        Clock clock
    ) {
        this(workerRegistry, messageDecoder, messageValidator, sessionBindings, clock, null);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        try {
            WorkerMessage decoded = messageDecoder.decode(message.getPayload());
            if (decoded instanceof RegisterMessage registerMessage) {
                handleRegister(session, registerMessage);
            } else if (decoded instanceof HeartbeatMessage heartbeatMessage) {
                handleHeartbeat(session, heartbeatMessage);
            } else if (decoded instanceof ExecuteResultMessage resultMessage) {
                handleExecuteResult(session, resultMessage);
            } else if (decoded instanceof ExecuteStartedMessage startedMessage) {
                handleExecuteStarted(session, startedMessage);
            } else {
                throw new ProtocolException(CloseStatus.NOT_ACCEPTABLE, "unsupported message type");
            }
        } catch (ProtocolException error) {
            close(session, error.closeStatus());
        } catch (RuntimeException error) {
            log.error("Unexpected worker message failure sessionId={}", session.getId(), error);
            close(session, CloseStatus.SERVER_ERROR);
        }
    }

    private void handleExecuteStarted(
        WebSocketSession session,
        ExecuteStartedMessage message
    ) {
        if (jobService == null) {
            throw new ProtocolException(CloseStatus.NOT_ACCEPTABLE, "EXECUTE_STARTED unsupported");
        }
        String workerId = sessionBindings.findWorkerId(session.getId())
            .orElseThrow(() -> new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "REGISTER required"
            ));
        JobProgressResult result = jobService.started(workerId, session.getId(), message);
        if (result != JobProgressResult.APPLIED) {
            throw new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "stale or unknown EXECUTE_STARTED"
            );
        }
        log.info(
            "Job running jobId={} workerId={} containerId={} containerName={} workload={} imageDigest={}",
            message.jobId(), workerId, message.containerId(), message.containerName(),
            message.workload(), message.imageDigest()
        );
    }

    private void handleExecuteResult(
        WebSocketSession session,
        ExecuteResultMessage message
    ) {
        if (jobService == null) {
            throw new ProtocolException(CloseStatus.NOT_ACCEPTABLE, "EXECUTE_RESULT unsupported");
        }
        String workerId = sessionBindings.findWorkerId(session.getId())
            .orElseThrow(() -> new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "REGISTER required"
            ));
        JobCompletionResult result = jobService.complete(workerId, session.getId(), message);
        if (result != JobCompletionResult.APPLIED) {
            throw new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "stale or unknown EXECUTE_RESULT"
            );
        }
        log.info(
            "Job result workerId={} jobId={} status={} containerId={} containerName={} "
                + "workload={} imageDigest={} runtimeMs={} resultChecksum={}",
            workerId, message.jobId(), message.status(), message.containerId(),
            message.containerName(), message.workload(), message.imageDigest(),
            message.runtimeMs(), message.resultChecksum()
        );
    }

    @Override
    protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
        close(session, CloseStatus.NOT_ACCEPTABLE);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        sessionBindings.remove(session.getId()).ifPresent(workerId -> {
            boolean disconnected = jobService == null
                ? workerRegistry.disconnectIfActive(workerId, session.getId())
                : jobService.workerDisconnected(workerId, session.getId());
            if (disconnected) {
                log.info("Worker offline workerId={} reason=DISCONNECTED", workerId);
            }
        });
    }

    private void handleRegister(WebSocketSession session, RegisterMessage message) {
        WorkerRegistration registration = messageValidator.validate(message);
        Object authenticatedWorkerId = session.getAttributes() == null ? null
            : session.getAttributes().get(JobSecurityConfig.AUTHENTICATED_WORKER_ID);
        if (authenticatedWorkerId != null
            && !registration.workerId().equals(authenticatedWorkerId)) {
            throw new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "authenticated workerId mismatch"
            );
        }
        if (!sessionBindings.bind(session.getId(), registration.workerId())) {
            throw new ProtocolException(CloseStatus.POLICY_VIOLATION, "session already registered");
        }

        try {
            RegistrationResult result = workerRegistry.register(
                registration,
                session,
                clock.instant()
            );
            result.replacedSession()
                .filter(previous -> previous != session)
                .ifPresent(previous -> {
                    log.info(
                        "Worker session replaced workerId={} oldSessionId={} newSessionId={}",
                        registration.workerId(),
                        previous.getId(),
                        session.getId()
                    );
                    close(previous, REPLACED_BY_NEWER_CONNECTION);
                });
        } catch (RuntimeException error) {
            sessionBindings.remove(session.getId());
            throw error;
        }

        log.info(
            "Worker registered workerId={} gpu=\"{}\" cuda={} connectionStatus=ONLINE workerStatus={}",
            registration.workerId(),
            registration.gpuModel(),
            registration.cudaAvailable(),
            registration.workerStatus()
        );
    }

    private void handleHeartbeat(WebSocketSession session, HeartbeatMessage message) {
        String boundWorkerId = sessionBindings.findWorkerId(session.getId())
            .orElseThrow(() -> new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "REGISTER required"
            ));
        if (!boundWorkerId.equals(message.workerId())) {
            throw new ProtocolException(CloseStatus.POLICY_VIOLATION, "workerId mismatch");
        }

        WorkerRecord registered = workerRegistry.findById(boundWorkerId)
            .orElseThrow(() -> new ProtocolException(
                CloseStatus.POLICY_VIOLATION,
                "worker is not registered"
            ));
        WorkerHeartbeat heartbeat = messageValidator.validate(
            message,
            registered.totalVramMb()
        );
        HeartbeatResult result = workerRegistry.updateHeartbeat(
            heartbeat,
            session.getId(),
            clock.instant()
        );
        if (result != HeartbeatResult.APPLIED) {
            throw new ProtocolException(CloseStatus.POLICY_VIOLATION, "inactive worker session");
        }
        log.debug(
            "Worker heartbeat workerId={} freeVramMb={} gpuUtilization={} workerStatus={}",
            heartbeat.workerId(),
            heartbeat.freeVramMb(),
            heartbeat.gpuUtilization(),
            heartbeat.workerStatus()
        );
    }

    private void close(WebSocketSession session, CloseStatus closeStatus) {
        if (!session.isOpen()) {
            return;
        }
        try {
            session.close(closeStatus);
        } catch (IOException error) {
            log.debug("Failed to close worker session sessionId={}", session.getId(), error);
        }
    }
}
