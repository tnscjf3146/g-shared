package com.gpucompute.poc.backend.registry;

public enum HeartbeatResult {
    APPLIED,
    UNKNOWN_WORKER,
    STALE_SESSION,
    OFFLINE
}
