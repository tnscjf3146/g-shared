package com.gpucompute.poc.backend.registry;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.UUID;

import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.domain.WorkerHeartbeat;

@Component
public class WorkerRegistry {

    private final ConcurrentHashMap<String, WorkerRecord> workers = new ConcurrentHashMap<>();

    public RegistrationResult register(
        WorkerRegistration registration,
        WebSocketSession session,
        Instant observedAt
    ) {
        AtomicReference<WebSocketSession> replacedSession = new AtomicReference<>();
        WorkerRecord current = workers.compute(registration.workerId(), (workerId, previous) -> {
            if (previous != null) {
                replacedSession.set(previous.activeSession());
            }
            return WorkerRecord.online(registration, session, observedAt);
        });

        return new RegistrationResult(current, Optional.ofNullable(replacedSession.get()));
    }

    public Optional<WorkerRecord> findById(String workerId) {
        return Optional.ofNullable(workers.get(workerId));
    }

    public List<WorkerRecord> findAll() {
        return workers.values().stream()
            .sorted(Comparator.comparing(WorkerRecord::workerId))
            .toList();
    }

    public HeartbeatResult updateHeartbeat(
        WorkerHeartbeat heartbeat,
        String sessionId,
        Instant observedAt
    ) {
        AtomicReference<HeartbeatResult> result = new AtomicReference<>(HeartbeatResult.UNKNOWN_WORKER);
        workers.computeIfPresent(heartbeat.workerId(), (workerId, current) -> {
            if (current.connectionStatus() == ConnectionStatus.OFFLINE) {
                result.set(HeartbeatResult.OFFLINE);
                return current;
            }
            if (current.activeSession() == null
                || !current.activeSession().getId().equals(sessionId)) {
                result.set(HeartbeatResult.STALE_SESSION);
                return current;
            }

            result.set(HeartbeatResult.APPLIED);
            return current.withHeartbeat(heartbeat, observedAt);
        });
        return result.get();
    }

    public List<TimedOutSession> expireStale(Instant now, Duration timeout) {
        if (timeout.isNegative()) {
            throw new IllegalArgumentException("timeout must not be negative");
        }

        List<TimedOutSession> timedOutSessions = new ArrayList<>();
        for (String workerId : workers.keySet()) {
            workers.computeIfPresent(workerId, (id, current) -> {
                boolean expired = current.connectionStatus() == ConnectionStatus.ONLINE
                    && current.activeSession() != null
                    && !now.isBefore(current.lastHeartbeat().plus(timeout));
                if (!expired) {
                    return current;
                }

                timedOutSessions.add(new TimedOutSession(
                    id,
                    current.activeSession(),
                    current.activeJobId()
                ));
                return current.offline();
            });
        }
        return List.copyOf(timedOutSessions);
    }

    public boolean disconnectIfActive(String workerId, String sessionId) {
        return disconnectActive(workerId, sessionId).disconnected();
    }

    public WorkerDisconnectResult disconnectActive(String workerId, String sessionId) {
        AtomicReference<WorkerDisconnectResult> result = new AtomicReference<>(
            new WorkerDisconnectResult(false, null)
        );
        workers.computeIfPresent(workerId, (id, current) -> {
            boolean isActive = current.connectionStatus() == ConnectionStatus.ONLINE
                && current.activeSession() != null
                && current.activeSession().getId().equals(sessionId);
            if (!isActive) {
                return current;
            }

            result.set(new WorkerDisconnectResult(true, current.activeJobId()));
            return current.offline();
        });
        return result.get();
    }

    public JobReservationResult reserveJob(String workerId, UUID jobId, String workload) {
        AtomicReference<JobReservationResult> result = new AtomicReference<>(
            JobReservationResult.rejected(JobReservationResult.Status.UNKNOWN_WORKER)
        );
        workers.computeIfPresent(workerId, (id, current) -> {
            if (current.connectionStatus() != ConnectionStatus.ONLINE
                || current.activeSession() == null) {
                result.set(JobReservationResult.rejected(JobReservationResult.Status.OFFLINE));
            } else if (!current.cudaAvailable()) {
                result.set(JobReservationResult.rejected(JobReservationResult.Status.CUDA_UNAVAILABLE));
            } else if (!current.supportedWorkloads().contains(workload)) {
                result.set(JobReservationResult.rejected(JobReservationResult.Status.UNSUPPORTED_WORKLOAD));
            } else if (current.workerStatus() != com.gpucompute.poc.backend.domain.WorkerStatus.IDLE
                || current.activeJobId() != null) {
                result.set(JobReservationResult.rejected(JobReservationResult.Status.BUSY));
            } else {
                result.set(new JobReservationResult(
                    JobReservationResult.Status.RESERVED,
                    current.activeSession()
                ));
                return current.reserve(jobId);
            }
            return current;
        });
        return result.get();
    }

    public boolean releaseReservation(String workerId, UUID jobId, String sessionId) {
        AtomicBoolean released = new AtomicBoolean(false);
        workers.computeIfPresent(workerId, (id, current) -> {
            if (jobId.equals(current.activeJobId())
                && sessionId.equals(current.activeJobSessionId())) {
                released.set(true);
                return current.releaseJob();
            }
            return current;
        });
        return released.get();
    }

    public JobCompletionResult completeJob(String workerId, UUID jobId, String sessionId) {
        AtomicReference<JobCompletionResult> result = new AtomicReference<>(
            JobCompletionResult.UNKNOWN_WORKER
        );
        workers.computeIfPresent(workerId, (id, current) -> {
            if (current.activeSession() == null
                || !sessionId.equals(current.activeSession().getId())
                || !sessionId.equals(current.activeJobSessionId())) {
                result.set(JobCompletionResult.STALE_SESSION);
                return current;
            }
            if (!jobId.equals(current.activeJobId())) {
                result.set(JobCompletionResult.STALE_JOB);
                return current;
            }
            result.set(JobCompletionResult.APPLIED);
            return current.releaseJob();
        });
        return result.get();
    }

    public JobProgressResult validateJobProgress(
        String workerId,
        UUID jobId,
        String sessionId
    ) {
        AtomicReference<JobProgressResult> result = new AtomicReference<>(
            JobProgressResult.UNKNOWN_WORKER
        );
        workers.computeIfPresent(workerId, (id, current) -> {
            if (current.activeSession() == null
                || !sessionId.equals(current.activeSession().getId())
                || !sessionId.equals(current.activeJobSessionId())) {
                result.set(JobProgressResult.STALE_SESSION);
            } else if (!jobId.equals(current.activeJobId())) {
                result.set(JobProgressResult.STALE_JOB);
            } else {
                result.set(JobProgressResult.APPLIED);
            }
            return current;
        });
        return result.get();
    }
}
