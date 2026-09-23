package com.gshared.back.job.realtime;

import java.time.Instant;

public record JobRealtimeEvent(
        String type,
        Long jobId,
        String status,
        Double progress,
        Integer currentStep,
        Integer totalSteps,
        Boolean resultAvailable,
        String reason,
        Instant updatedAt
) {
    public static JobRealtimeEvent progress(Long jobId, Double progress,
                                            Integer currentStep, Integer totalSteps) {
        return new JobRealtimeEvent("JOB_PROGRESS", jobId, "RUNNING", progress,
                currentStep, totalSteps, null, null, Instant.now());
    }

    public static JobRealtimeEvent completed(Long jobId) {
        return new JobRealtimeEvent("JOB_COMPLETED", jobId, "COMPLETED", 100.0,
                null, null, true, null, Instant.now());
    }

    public static JobRealtimeEvent failed(Long jobId, String reason) {
        return new JobRealtimeEvent("JOB_FAILED", jobId, "FAILED", null,
                null, null, null, reason, Instant.now());
    }

    public static JobRealtimeEvent cancelled(Long jobId) {
        return new JobRealtimeEvent("JOB_CANCELLED", jobId, "CANCELLED", null,
                null, null, null, null, Instant.now());
    }
}
