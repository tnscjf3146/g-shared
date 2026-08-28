package com.gpucompute.poc.backend.protocol;

import java.util.Set;

import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Component
public class WorkerMessageDecoder {

    private final ObjectMapper objectMapper;

    public WorkerMessageDecoder(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    public WorkerMessage decode(String json) {
        try {
            JsonNode root = objectMapper.readTree(json);
            if (root == null || !root.isObject()) {
                throw badData("message must be a JSON object");
            }

            JsonNode typeNode = root.get("type");
            if (typeNode == null || !typeNode.isString() || typeNode.stringValue().isBlank()) {
                throw badData("type is required");
            }

            WorkerMessageType type;
            try {
                type = WorkerMessageType.valueOf(typeNode.stringValue());
            } catch (IllegalArgumentException error) {
                throw new ProtocolException(CloseStatus.NOT_ACCEPTABLE, "unknown message type");
            }

            if (type == WorkerMessageType.HEARTBEAT) {
                requireIntegralFields(root, "freeVramMb", "gpuUtilization");
                return objectMapper.treeToValue(root, HeartbeatMessage.class);
            }

            if (type == WorkerMessageType.EXECUTE_RESULT) {
                requireOnlyFields(root, Set.of(
                    "type", "jobId", "status", "gpuModel", "runtimeMs",
                    "resultChecksum", "containerExitCode", "imageCacheHit",
                    "errorCode", "errorMessage", "containerId", "containerName",
                    "workload", "imageDigest", "iterations", "matrixSize",
                    "targetDurationSeconds"
                ));
                requireIntegralFields(root, "runtimeMs", "containerExitCode", "iterations",
                    "matrixSize", "targetDurationSeconds");
                ExecuteResultMessage result = objectMapper.treeToValue(
                    root,
                    ExecuteResultMessage.class
                );
                validateResult(result);
                return result;
            }

            if (type == WorkerMessageType.EXECUTE_STARTED) {
                requireOnlyFields(root, Set.of(
                    "type", "jobId", "workerId", "containerId", "containerName",
                    "workload", "imageDigest"
                ));
                ExecuteStartedMessage started = objectMapper.treeToValue(
                    root,
                    ExecuteStartedMessage.class
                );
                validateStarted(started);
                return started;
            }

            requireIntegralFields(
                root,
                "totalVramMb",
                "freeVramMb",
                "gpuUtilization",
                "hourlyPrice"
            );
            return objectMapper.treeToValue(root, RegisterMessage.class);
        } catch (ProtocolException error) {
            throw error;
        } catch (RuntimeException error) {
            throw new ProtocolException(CloseStatus.BAD_DATA, "invalid JSON message", error);
        }
    }

    private static void requireOnlyFields(JsonNode root, Set<String> allowed) {
        root.propertyNames().forEach(name -> {
            if (!allowed.contains(name)) {
                throw badData("unexpected field: " + name);
            }
        });
    }

    private static void validateResult(ExecuteResultMessage result) {
        if (result.jobId() == null || result.status() == null) {
            throw badData("jobId and status are required");
        }
        if (result.status() == ExecuteResultStatus.SUCCESS) {
            if (result.gpuModel() == null || result.gpuModel().isBlank()
                || result.runtimeMs() == null || result.runtimeMs() <= 0
                || result.resultChecksum() == null
                || !result.resultChecksum().matches("[0-9a-f]{64}")
                || result.containerExitCode() == null || result.containerExitCode() != 0
                || result.imageCacheHit() == null) {
                throw badData("invalid SUCCESS result");
            }
            if (result.iterations() != null && result.iterations() <= 0
                || result.matrixSize() != null && result.matrixSize() <= 0
                || result.targetDurationSeconds() != null
                    && result.targetDurationSeconds() <= 0) {
                throw badData("invalid SUCCESS execution details");
            }
        } else if (result.errorCode() == null || result.errorCode().isBlank()
            || result.errorMessage() == null || result.errorMessage().isBlank()) {
            throw badData("invalid FAILED result");
        }
    }

    private static void validateStarted(ExecuteStartedMessage started) {
        if (started.jobId() == null
            || isBlank(started.workerId())
            || isBlank(started.containerId())
            || isBlank(started.containerName())
            || !started.containerName().startsWith("gpu-poc-job-")
            || isBlank(started.workload())
            || isBlank(started.imageDigest())
            || !started.imageDigest().matches("[^\\s]+@sha256:[0-9a-f]{64}")) {
            throw badData("invalid EXECUTE_STARTED proof");
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private static void requireIntegralFields(JsonNode root, String... fieldNames) {
        for (String fieldName : fieldNames) {
            JsonNode value = root.get(fieldName);
            if (value != null && !value.isNull() && !value.isIntegralNumber()) {
                throw badData(fieldName + " must be an integer");
            }
        }
    }

    private static ProtocolException badData(String reason) {
        return new ProtocolException(CloseStatus.BAD_DATA, reason);
    }
}
