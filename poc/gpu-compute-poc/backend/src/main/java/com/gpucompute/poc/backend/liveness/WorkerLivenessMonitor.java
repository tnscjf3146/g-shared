package com.gpucompute.poc.backend.liveness;

import java.io.IOException;
import java.time.Clock;
import java.time.Duration;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.registry.TimedOutSession;
import com.gpucompute.poc.backend.registry.WorkerRegistry;
import com.gpucompute.poc.backend.job.JobService;

@Component
public class WorkerLivenessMonitor {

    private static final Logger log = LoggerFactory.getLogger(WorkerLivenessMonitor.class);
    private static final CloseStatus HEARTBEAT_TIMEOUT =
        CloseStatus.POLICY_VIOLATION.withReason("heartbeat timeout");

    private final WorkerRegistry workerRegistry;
    private final Clock clock;
    private final Duration timeout;
    private final JobService jobService;

    @Autowired
    public WorkerLivenessMonitor(
        WorkerRegistry workerRegistry,
        Clock clock,
        @Value("${worker.liveness.timeout:15s}") Duration timeout,
        JobService jobService
    ) {
        this.workerRegistry = workerRegistry;
        this.clock = clock;
        this.timeout = timeout;
        this.jobService = jobService;
    }

    public WorkerLivenessMonitor(
        WorkerRegistry workerRegistry,
        Clock clock,
        Duration timeout
    ) {
        this(workerRegistry, clock, timeout, null);
    }

    @Scheduled(fixedDelayString = "${worker.liveness.scan-interval-ms:1000}")
    public void scan() {
        for (TimedOutSession timedOut : workerRegistry.expireStale(clock.instant(), timeout)) {
            if (jobService != null && timedOut.activeJobId() != null) {
                jobService.markResultUnknown(
                    timedOut.activeJobId(),
                    "Worker heartbeat timed out"
                );
            }
            log.info(
                "Worker offline workerId={} reason=HEARTBEAT_TIMEOUT",
                timedOut.workerId()
            );
            close(timedOut.session());
        }
    }

    private void close(WebSocketSession session) {
        if (!session.isOpen()) {
            return;
        }
        try {
            session.close(HEARTBEAT_TIMEOUT);
        } catch (IOException error) {
            log.debug("Failed to close timed-out worker session sessionId={}", session.getId(), error);
        }
    }
}
