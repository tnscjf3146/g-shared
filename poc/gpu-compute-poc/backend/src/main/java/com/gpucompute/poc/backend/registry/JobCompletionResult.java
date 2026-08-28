package com.gpucompute.poc.backend.registry;

public enum JobCompletionResult {
    APPLIED,
    UNKNOWN_WORKER,
    STALE_SESSION,
    STALE_JOB
}
