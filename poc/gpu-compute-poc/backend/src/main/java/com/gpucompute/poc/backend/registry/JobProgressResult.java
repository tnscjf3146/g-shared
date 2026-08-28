package com.gpucompute.poc.backend.registry;

public enum JobProgressResult {
    APPLIED,
    UNKNOWN_WORKER,
    STALE_SESSION,
    STALE_JOB
}
