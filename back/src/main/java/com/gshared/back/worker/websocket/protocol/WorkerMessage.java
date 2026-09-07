package com.gshared.back.worker.websocket.protocol;

import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.JsonNode;

/**
 * WebSocket 공통 메시지 봉투.
 * <pre>
 * { "type": "JOB_PROGRESS", "jobId": "job-456", "timestamp": "...", "payload": { ... } }
 * </pre>
 * payload 는 메시지 종류마다 내용이 달라서 특정 클래스로 못 박지 않고
 * JsonNode(유연한 JSON 트리)로 받아둔다. 필요할 때 payload.get("progress") 식으로 꺼낸다.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)   // null 필드(jobId 등)는 JSON에서 생략
public record WorkerMessage(
        WorkerMessageType type,
        String jobId,        // Job 관련 메시지에만 존재
        String timestamp,    // ISO 8601
        JsonNode payload
) {
    /** 발신용(Backend → Worker) 봉투 생성 헬퍼. jobId 없는 메시지. */
    public static WorkerMessage of(WorkerMessageType type, String timestamp, JsonNode payload) {
        return new WorkerMessage(type, null, timestamp, payload);
    }

    /** 발신용. jobId 있는 메시지(JOB_ASSIGN/JOB_CANCEL 등). */
    public static WorkerMessage of(WorkerMessageType type, String jobId, String timestamp, JsonNode payload) {
        return new WorkerMessage(type, jobId, timestamp, payload);
    }
}
