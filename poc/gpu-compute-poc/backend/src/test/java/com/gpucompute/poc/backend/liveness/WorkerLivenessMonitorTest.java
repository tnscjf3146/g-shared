package com.gpucompute.poc.backend.liveness;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.Set;

import org.junit.jupiter.api.Test;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerStatus;
import com.gpucompute.poc.backend.registry.WorkerRegistry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WorkerLivenessMonitorTest {

    @Test
    void scanUsesInjectedClockAndClosesExactTimedOutSessionWithoutSleeping() throws Exception {
        Instant registeredAt = Instant.parse("2026-08-27T00:00:00Z");
        MutableClock clock = new MutableClock(registeredAt);
        WorkerRegistry registry = new WorkerRegistry();
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("session-A");
        when(session.isOpen()).thenReturn(true);
        registry.register(registration(), session, registeredAt);
        WorkerLivenessMonitor monitor = new WorkerLivenessMonitor(
            registry,
            clock,
            Duration.ofSeconds(15)
        );

        clock.set(registeredAt.plusSeconds(14));
        monitor.scan();
        assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
            .isEqualTo(ConnectionStatus.ONLINE);

        clock.set(registeredAt.plusSeconds(15));
        monitor.scan();

        assertThat(registry.findById("worker-001").orElseThrow().connectionStatus())
            .isEqualTo(ConnectionStatus.OFFLINE);
        verify(session).close(CloseStatus.POLICY_VIOLATION.withReason("heartbeat timeout"));
    }

    private static WorkerRegistration registration() {
        return new WorkerRegistration(
            "worker-001", "GPU", 8188, 7800, 0, true,
            Set.of("EMBEDDING"), 600, WorkerStatus.IDLE
        );
    }

    private static final class MutableClock extends Clock {
        private Instant instant;

        private MutableClock(Instant instant) {
            this.instant = instant;
        }

        void set(Instant instant) {
            this.instant = instant;
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return instant;
        }
    }
}
