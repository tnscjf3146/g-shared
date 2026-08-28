package com.gpucompute.poc.backend.registry;

import java.util.UUID;

public record WorkerDisconnectResult(boolean disconnected, UUID activeJobId) {}
