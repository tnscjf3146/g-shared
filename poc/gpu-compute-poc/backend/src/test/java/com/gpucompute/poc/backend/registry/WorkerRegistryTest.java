package com.gpucompute.poc.backend.registry;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.Test;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.domain.WorkerHeartbeat;
import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerStatus;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class WorkerRegistryTest {

    private final WorkerRegistry registry = new WorkerRegistry();

    @Test
    void registerStoresCompleteOnlineWorkerState() {
        WebSocketSession session = session("session-A");
        Instant observedAt = Instant.parse("2026-08-27T00:00:00Z");

        RegistrationResult result = registry.register(registration("worker-001"), session, observedAt);

        WorkerRecord record = result.current();
        assertThat(record.workerId()).isEqualTo("worker-001");
        assertThat(record.gpuModel()).isEqualTo("NVIDIA Test GPU");
        assertThat(record.totalVramMb()).isEqualTo(8188);
        assertThat(record.freeVramMb()).isEqualTo(7800);
        assertThat(record.gpuUtilization()).isEqualTo(4);
        assertThat(record.cudaAvailable()).isTrue();
        assertThat(record.supportedWorkloads()).containsExactly("EMBEDDING");
        assertThat(record.hourlyPrice()).isEqualTo(600);
        assertThat(record.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
        assertThat(record.workerStatus()).isEqualTo(WorkerStatus.IDLE);
        assertThat(record.lastHeartbeat()).isEqualTo(observedAt);
        assertThat(record.activeSession()).isSameAs(session);
        assertThat(result.replacedSession()).isEmpty();
        assertThat(registry.findById("worker-001")).contains(record);
    }

    @Test
    void findByIdReturnsEmptyForUnknownWorker() {
        assertThat(registry.findById("missing")).isEmpty();
    }

    @Test
    void findAllReturnsSortedImmutableSnapshot() {
        registry.register(registration("worker-002"), session("session-B"), Instant.EPOCH);
        registry.register(registration("worker-001"), session("session-A"), Instant.EPOCH);

        List<WorkerRecord> snapshot = registry.findAll();

        assertThat(snapshot).extracting(WorkerRecord::workerId)
            .containsExactly("worker-001", "worker-002");
        assertThatThrownBy(() -> snapshot.add(snapshot.getFirst()))
            .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void differentWorkerIdsRemainOnlineSimultaneously() {
        registry.register(registration("worker-001"), session("session-A"), Instant.EPOCH);
        registry.register(registration("worker-002"), session("session-B"), Instant.EPOCH);

        assertThat(registry.findAll()).extracting(WorkerRecord::workerId)
            .containsExactly("worker-001", "worker-002");
        assertThat(registry.findAll()).extracting(WorkerRecord::connectionStatus)
            .containsOnly(ConnectionStatus.ONLINE);
    }

    @Test
    void activeHeartbeatUpdatesOnlyLiveGpuFieldsStatusAndTimestamp() {
        WebSocketSession session = session("session-A");
        Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
        Instant heartbeatAt = registeredAt.plusSeconds(5);
        registry.register(registration("worker-001"), session, registeredAt);

        HeartbeatResult result = registry.updateHeartbeat(
            new WorkerHeartbeat("worker-001", 7600, 83, WorkerStatus.BUSY),
            "session-A",
            heartbeatAt
        );

        WorkerRecord record = registry.findById("worker-001").orElseThrow();
        assertThat(result).isEqualTo(HeartbeatResult.APPLIED);
        assertThat(record.freeVramMb()).isEqualTo(7600);
        assertThat(record.gpuUtilization()).isEqualTo(83);
        assertThat(record.workerStatus()).isEqualTo(WorkerStatus.BUSY);
        assertThat(record.lastHeartbeat()).isEqualTo(heartbeatAt);
        assertThat(record.gpuModel()).isEqualTo("NVIDIA Test GPU");
        assertThat(record.totalVramMb()).isEqualTo(8188);
        assertThat(record.hourlyPrice()).isEqualTo(600);
        assertThat(record.activeSession()).isSameAs(session);
    }

    @Test
    void heartbeatRejectsUnknownStaleAndOfflineWorkers() {
        Instant observedAt = Instant.parse("2026-08-27T00:00:00Z");
        WorkerHeartbeat heartbeat = new WorkerHeartbeat("worker-001", 7600, 4, WorkerStatus.IDLE);

        assertThat(registry.updateHeartbeat(heartbeat, "session-A", observedAt))
            .isEqualTo(HeartbeatResult.UNKNOWN_WORKER);

        registry.register(registration("worker-001"), session("session-A"), observedAt);
        assertThat(registry.updateHeartbeat(heartbeat, "session-B", observedAt.plusSeconds(5)))
            .isEqualTo(HeartbeatResult.STALE_SESSION);

        registry.expireStale(observedAt.plusSeconds(15), Duration.ofSeconds(15));
        assertThat(registry.updateHeartbeat(heartbeat, "session-A", observedAt.plusSeconds(16)))
            .isEqualTo(HeartbeatResult.OFFLINE);
        assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
            .isEqualTo(ConnectionStatus.OFFLINE);
    }

    @Test
    void timeoutOccursAtExactBoundaryAndPreservesLastRuntimeState() {
        WebSocketSession session = session("session-A");
        Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
        registry.register(registration("worker-001", WorkerStatus.BUSY), session, registeredAt);

        assertThat(registry.expireStale(
            registeredAt.plusSeconds(14),
            Duration.ofSeconds(15)
        )).isEmpty();

        assertThat(registry.expireStale(
            registeredAt.plusSeconds(15),
            Duration.ofSeconds(15)
        )).extracting(TimedOutSession::session).containsExactly(session);

        WorkerRecord offline = registry.findById("worker-001").orElseThrow();
        assertThat(offline.connectionStatus()).isEqualTo(ConnectionStatus.OFFLINE);
        assertThat(offline.workerStatus()).isEqualTo(WorkerStatus.BUSY);
        assertThat(offline.lastHeartbeat()).isEqualTo(registeredAt);
        assertThat(offline.activeSession()).isNull();
    }

    @Test
    void simultaneousHeartbeatAndTimeoutProduceOneAtomicOutcome() throws Exception {
        WebSocketSession session = session("session-A");
        Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
        Instant boundary = registeredAt.plusSeconds(15);
        registry.register(registration("worker-001"), session, registeredAt);

        ExecutorService executor = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        try {
            Future<HeartbeatResult> heartbeatFuture = executor.submit(() -> {
                ready.countDown();
                start.await();
                return registry.updateHeartbeat(
                    new WorkerHeartbeat("worker-001", 7500, 20, WorkerStatus.BUSY),
                    "session-A",
                    boundary
                );
            });
            Future<List<TimedOutSession>> timeoutFuture = executor.submit(() -> {
                ready.countDown();
                start.await();
                return registry.expireStale(boundary, Duration.ofSeconds(15));
            });

            assertThat(ready.await(2, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            HeartbeatResult heartbeatResult = heartbeatFuture.get(2, TimeUnit.SECONDS);
            List<TimedOutSession> timedOut = timeoutFuture.get(2, TimeUnit.SECONDS);
            WorkerRecord record = registry.findById("worker-001").orElseThrow();

            if (record.connectionStatus() == ConnectionStatus.ONLINE) {
                assertThat(heartbeatResult).isEqualTo(HeartbeatResult.APPLIED);
                assertThat(timedOut).isEmpty();
                assertThat(record.lastHeartbeat()).isEqualTo(boundary);
                assertThat(record.activeSession()).isSameAs(session);
            } else {
                assertThat(heartbeatResult).isEqualTo(HeartbeatResult.OFFLINE);
                assertThat(timedOut).extracting(TimedOutSession::session).containsExactly(session);
                assertThat(record.lastHeartbeat()).isEqualTo(registeredAt);
                assertThat(record.activeSession()).isNull();
            }
        } finally {
            executor.shutdownNow();
        }
    }

    @Test
    void lastConnectionWinsAndStaleDisconnectCannotOfflineReplacement() {
        WebSocketSession sessionA = session("session-A");
        WebSocketSession sessionB = session("session-B");
        Instant firstRegistration = Instant.parse("2026-08-27T00:00:00Z");
        Instant secondRegistration = firstRegistration.plusSeconds(1);
        registry.register(registration("worker-001"), sessionA, firstRegistration);

        RegistrationResult replacement = registry.register(
            registration("worker-001", WorkerStatus.BUSY),
            sessionB,
            secondRegistration
        );

        assertThat(replacement.replacedSession()).contains(sessionA);
        assertThat(registry.findById("worker-001").orElseThrow().activeSession())
            .isSameAs(sessionB);

        assertThat(registry.disconnectIfActive("worker-001", "session-A")).isFalse();
        assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
            .isEqualTo(ConnectionStatus.ONLINE);

        assertThat(registry.disconnectIfActive("worker-001", "session-B")).isTrue();
        WorkerRecord offline = registry.findById("worker-001").orElseThrow();
        assertThat(offline.connectionStatus()).isEqualTo(ConnectionStatus.OFFLINE);
        assertThat(offline.workerStatus()).isEqualTo(WorkerStatus.BUSY);
        assertThat(offline.activeSession()).isNull();
    }

    @Test
    void timeoutAndReplacementRaceNeverInvalidatesNewSession() throws Exception {
        WebSocketSession sessionA = session("session-A");
        WebSocketSession sessionB = session("session-B");
        Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
        Instant boundary = registeredAt.plusSeconds(15);
        registry.register(registration("worker-001"), sessionA, registeredAt);

        ExecutorService executor = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        try {
            Future<RegistrationResult> registrationFuture = executor.submit(() -> {
                ready.countDown();
                start.await();
                return registry.register(
                    registration("worker-001"),
                    sessionB,
                    boundary
                );
            });
            Future<List<TimedOutSession>> timeoutFuture = executor.submit(() -> {
                ready.countDown();
                start.await();
                return registry.expireStale(boundary, Duration.ofSeconds(15));
            });

            assertThat(ready.await(2, TimeUnit.SECONDS)).isTrue();
            start.countDown();
            RegistrationResult replacement = registrationFuture.get(2, TimeUnit.SECONDS);
            List<TimedOutSession> timedOut = timeoutFuture.get(2, TimeUnit.SECONDS);

            WorkerRecord record = registry.findById("worker-001").orElseThrow();
            assertThat(record.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
            assertThat(record.activeSession()).isSameAs(sessionB);
            assertThat(record.lastHeartbeat()).isEqualTo(boundary);

            List<WebSocketSession> invalidated = new java.util.ArrayList<>();
            replacement.replacedSession().ifPresent(invalidated::add);
            timedOut.stream().map(TimedOutSession::session).forEach(invalidated::add);
            assertThat(invalidated).containsExactly(sessionA);
            assertThat(invalidated).doesNotContain(sessionB);
        } finally {
            executor.shutdownNow();
        }
    }

    @Test
    void timedOutWorkerReturnsOnlineOnlyAfterNewSessionRegisters() {
        WebSocketSession sessionA = session("session-A");
        WebSocketSession sessionB = session("session-B");
        Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
        registry.register(registration("worker-001"), sessionA, registeredAt);
        registry.expireStale(registeredAt.plusSeconds(15), Duration.ofSeconds(15));

        HeartbeatResult lateHeartbeat = registry.updateHeartbeat(
            new WorkerHeartbeat("worker-001", 7600, 4, WorkerStatus.IDLE),
            "session-A",
            registeredAt.plusSeconds(16)
        );
        assertThat(lateHeartbeat).isEqualTo(HeartbeatResult.OFFLINE);
        assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
            .isEqualTo(ConnectionStatus.OFFLINE);

        registry.register(
            registration("worker-001"),
            sessionB,
            registeredAt.plusSeconds(17)
        );

        WorkerRecord online = registry.findById("worker-001").orElseThrow();
        assertThat(online.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
        assertThat(online.activeSession()).isSameAs(sessionB);
    }

    @Test
    void staleSessionCannotCompleteNewSessionsJob() {
        WebSocketSession sessionA = session("session-A");
        WebSocketSession sessionB = session("session-B");
        registry.register(registration("worker-001"), sessionA, Instant.EPOCH);
        registry.register(registration("worker-001"), sessionB, Instant.EPOCH.plusSeconds(1));
        java.util.UUID jobId = java.util.UUID.randomUUID();
        assertThat(registry.reserveJob("worker-001", jobId, "EMBEDDING").status())
            .isEqualTo(JobReservationResult.Status.RESERVED);

        assertThat(registry.completeJob("worker-001", jobId, "session-A"))
            .isEqualTo(JobCompletionResult.STALE_SESSION);
        assertThat(registry.findById("worker-001").orElseThrow().workerStatus())
            .isEqualTo(WorkerStatus.BUSY);

        assertThat(registry.completeJob("worker-001", jobId, "session-B"))
            .isEqualTo(JobCompletionResult.APPLIED);
        assertThat(registry.findById("worker-001").orElseThrow().workerStatus())
            .isEqualTo(WorkerStatus.IDLE);
    }

    private static WorkerRegistration registration(String workerId) {
        return registration(workerId, WorkerStatus.IDLE);
    }

    private static WorkerRegistration registration(String workerId, WorkerStatus workerStatus) {
        return new WorkerRegistration(
            workerId,
            "NVIDIA Test GPU",
            8188,
            7800,
            4,
            true,
            Set.of("EMBEDDING"),
            600,
            workerStatus
        );
    }

    private static WebSocketSession session(String id) {
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn(id);
        return session;
    }
}
