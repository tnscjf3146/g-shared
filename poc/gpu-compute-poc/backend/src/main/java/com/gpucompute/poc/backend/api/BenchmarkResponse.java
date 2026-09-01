package com.gpucompute.poc.backend.api;

import java.time.Instant;

import com.gpucompute.poc.backend.domain.WorkerBenchmark;

public record BenchmarkResponse(
    String version,
    double score,
    double throughputTflops,
    int matrixSize,
    String precision,
    Instant measuredAt
) {
    static BenchmarkResponse from(WorkerBenchmark benchmark) {
        if (benchmark == null) {
            return null;
        }
        return new BenchmarkResponse(
            benchmark.version(),
            benchmark.score(),
            benchmark.throughputTflops(),
            benchmark.matrixSize(),
            benchmark.precision(),
            benchmark.measuredAt()
        );
    }
}
