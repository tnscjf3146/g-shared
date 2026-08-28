package com.gpucompute.poc.backend.registry;

import java.time.Instant;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.domain.WorkerHeartbeat;
import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerStatus;

public record WorkerRecord(
    String workerId,
    String gpuModel,
    long totalVramMb,
    long freeVramMb,
    int gpuUtilization,
    boolean cudaAvailable,
    Set<String> supportedWorkloads,
    long hourlyPrice,
    ConnectionStatus connectionStatus,
    WorkerStatus workerStatus,
    Instant lastHeartbeat,
    WebSocketSession activeSession,
    UUID activeJobId,
    String activeJobSessionId
) {
    public WorkerRecord {
        Objects.requireNonNull(workerId, "workerId");
        Objects.requireNonNull(gpuModel, "gpuModel");
        supportedWorkloads = Set.copyOf(supportedWorkloads);
        Objects.requireNonNull(connectionStatus, "connectionStatus");
        Objects.requireNonNull(workerStatus, "workerStatus");
        Objects.requireNonNull(lastHeartbeat, "lastHeartbeat");
    }

    static WorkerRecord online(
        WorkerRegistration registration,
        WebSocketSession session,
        Instant observedAt
    ) {
        return new WorkerRecord(
            registration.workerId(),
            registration.gpuModel(),
            registration.totalVramMb(),
            registration.freeVramMb(),
            registration.gpuUtilization(),
            registration.cudaAvailable(),
            registration.supportedWorkloads(),
            registration.hourlyPrice(),
            ConnectionStatus.ONLINE,
            registration.workerStatus(),
            observedAt,
            session,
            null,
            null
        );
    }

    WorkerRecord withHeartbeat(WorkerHeartbeat heartbeat, Instant observedAt) {
        return new WorkerRecord(
            workerId,
            gpuModel,
            totalVramMb,
            heartbeat.freeVramMb(),
            heartbeat.gpuUtilization(),
            cudaAvailable,
            supportedWorkloads,
            hourlyPrice,
            ConnectionStatus.ONLINE,
            heartbeat.workerStatus(),
            observedAt,
            activeSession,
            activeJobId,
            activeJobSessionId
        );
    }

    WorkerRecord offline() {
        return new WorkerRecord(
            workerId,
            gpuModel,
            totalVramMb,
            freeVramMb,
            gpuUtilization,
            cudaAvailable,
            supportedWorkloads,
            hourlyPrice,
            ConnectionStatus.OFFLINE,
            workerStatus,
            lastHeartbeat,
            null,
            activeJobId,
            activeJobSessionId
        );
    }

    WorkerRecord reserve(UUID jobId) {
        return new WorkerRecord(workerId, gpuModel, totalVramMb, freeVramMb,
            gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice,
            connectionStatus, WorkerStatus.BUSY, lastHeartbeat, activeSession,
            jobId, activeSession.getId());
    }

    WorkerRecord releaseJob() {
        return new WorkerRecord(workerId, gpuModel, totalVramMb, freeVramMb,
            gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice,
            connectionStatus, WorkerStatus.IDLE, lastHeartbeat, activeSession,
            null, null);
    }
}
