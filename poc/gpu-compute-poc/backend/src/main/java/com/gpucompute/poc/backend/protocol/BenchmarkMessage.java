package com.gpucompute.poc.backend.protocol;

public record BenchmarkMessage(
    String version,
    Double score,
    Double throughputTflops,
    Integer matrixSize,
    String precision,
    String measuredAt
) {
}
