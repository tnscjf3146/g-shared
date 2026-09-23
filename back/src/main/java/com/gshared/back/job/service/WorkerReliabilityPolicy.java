package com.gshared.back.job.service;

import java.util.Set;

/** Worker 실행 환경이나 GPU 실행 자체에 귀속할 수 있는 실패만 신뢰도에 반영한다. */
public final class WorkerReliabilityPolicy {
    private static final Set<String> WORKER_FAILURES = Set.of(
            "RUNTIME_ERROR",
            "CONTAINER_FAILED",
            "WORKER_DISCONNECTED",
            "MODEL_PREPARATION_FAILED",
            "IMAGE_PULL_FAILED",
            "MODEL_LOAD_FAILED",
            "CUDA_UNAVAILABLE",
            "CUDA_OOM",
            "TRAINING_FAILED",
            "OUTPUT_VALIDATION_FAILED",
            "UNKNOWN_RUNTIME_EXIT"
    );

    private WorkerReliabilityPolicy() {}

    public static boolean isWorkerFailure(String code) {
        return code != null && WORKER_FAILURES.contains(code.trim().toUpperCase());
    }
}
