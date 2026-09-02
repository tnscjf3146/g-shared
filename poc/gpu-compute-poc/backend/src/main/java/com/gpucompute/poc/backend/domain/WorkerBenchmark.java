package com.gpucompute.poc.backend.domain;

import java.time.Instant;
import java.util.Objects;

/**
 * Supplier-reported benchmark data. The PoC validates its schema and range but
 * does not provide hardware attestation or protect against a modified Worker.
 */
public record WorkerBenchmark(
    String version,
    double score,
    double throughputTflops,
    int matrixSize,
    String precision,
    Instant measuredAt
) {
    public WorkerBenchmark {
        Objects.requireNonNull(version, "version");
        Objects.requireNonNull(precision, "precision");
        Objects.requireNonNull(measuredAt, "measuredAt");
    }
}
