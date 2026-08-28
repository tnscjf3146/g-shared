package com.gpucompute.poc.backend.job;

import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.stereotype.Component;

@Component
public class InMemoryJobRegistry {
    private final ConcurrentHashMap<UUID, JobRecord> jobs = new ConcurrentHashMap<>();

    public JobRecord save(JobRecord job) {
        jobs.put(job.jobId(), job);
        return job;
    }

    public Optional<JobRecord> findById(UUID jobId) {
        return Optional.ofNullable(jobs.get(jobId));
    }

    public JobRecord update(UUID jobId, java.util.function.UnaryOperator<JobRecord> update) {
        return jobs.computeIfPresent(jobId, (id, current) -> update.apply(current));
    }
}
