package com.gpucompute.poc.backend.protocol;

import java.util.LinkedHashSet;
import java.util.Set;

import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;

import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerHeartbeat;
import com.gpucompute.poc.backend.domain.WorkerStatus;

@Component
public class WorkerMessageValidator {

    public WorkerRegistration validate(RegisterMessage message) {
        require(message != null, "REGISTER body is required");
        require(message.type() == WorkerMessageType.REGISTER, "type must be REGISTER");
        requireNotBlank(message.workerId(), "workerId");
        requireNotBlank(message.gpuModel(), "gpuModel");
        require(message.totalVramMb() != null && message.totalVramMb() > 0, "totalVramMb must be greater than zero");
        require(
            message.freeVramMb() != null
                && message.freeVramMb() >= 0
                && message.freeVramMb() <= message.totalVramMb(),
            "freeVramMb is out of range"
        );
        require(
            message.gpuUtilization() != null
                && message.gpuUtilization() >= 0
                && message.gpuUtilization() <= 100,
            "gpuUtilization is out of range"
        );
        require(message.cudaAvailable() != null, "cudaAvailable is required");
        require(
            message.supportedWorkloads() != null && !message.supportedWorkloads().isEmpty(),
            "supportedWorkloads is required"
        );

        Set<String> workloads = new LinkedHashSet<>();
        for (String workload : message.supportedWorkloads()) {
            requireNotBlank(workload, "supportedWorkloads value");
            workloads.add(workload);
        }

        require(message.hourlyPrice() != null && message.hourlyPrice() >= 0, "hourlyPrice must be non-negative");
        WorkerStatus workerStatus = parseWorkerStatus(message.workerStatus());

        return new WorkerRegistration(
            message.workerId(),
            message.gpuModel(),
            message.totalVramMb(),
            message.freeVramMb(),
            message.gpuUtilization(),
            message.cudaAvailable(),
            workloads,
            message.hourlyPrice(),
            workerStatus
        );
    }

    public WorkerHeartbeat validate(HeartbeatMessage message, long registeredTotalVramMb) {
        require(message != null, "HEARTBEAT body is required");
        require(message.type() == WorkerMessageType.HEARTBEAT, "type must be HEARTBEAT");
        requireNotBlank(message.workerId(), "workerId");
        require(
            message.freeVramMb() != null
                && message.freeVramMb() >= 0
                && message.freeVramMb() <= registeredTotalVramMb,
            "freeVramMb is out of range"
        );
        require(
            message.gpuUtilization() != null
                && message.gpuUtilization() >= 0
                && message.gpuUtilization() <= 100,
            "gpuUtilization is out of range"
        );

        return new WorkerHeartbeat(
            message.workerId(),
            message.freeVramMb(),
            message.gpuUtilization(),
            parseWorkerStatus(message.workerStatus())
        );
    }

    private static WorkerStatus parseWorkerStatus(String value) {
        requireNotBlank(value, "workerStatus");
        try {
            return WorkerStatus.valueOf(value);
        } catch (IllegalArgumentException error) {
            throw badData("workerStatus must be IDLE or BUSY");
        }
    }

    private static void requireNotBlank(String value, String fieldName) {
        require(value != null && !value.isBlank(), fieldName + " must not be blank");
    }

    private static void require(boolean condition, String reason) {
        if (!condition) {
            throw badData(reason);
        }
    }

    private static ProtocolException badData(String reason) {
        return new ProtocolException(CloseStatus.BAD_DATA, reason);
    }
}
