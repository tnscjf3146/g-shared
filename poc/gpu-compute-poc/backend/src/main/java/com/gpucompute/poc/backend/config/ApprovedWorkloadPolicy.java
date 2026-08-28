package com.gpucompute.poc.backend.config;

import java.util.Set;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class ApprovedWorkloadPolicy {
    private final Set<String> workloads;

    public ApprovedWorkloadPolicy(
        @Value("${worker.workloads.approved:GPU_PYTORCH_TEST}") String approved
    ) {
        this.workloads = Set.of(approved.split(","));
    }

    public boolean isApproved(String workload) {
        return workload != null && workloads.contains(workload);
    }
}
