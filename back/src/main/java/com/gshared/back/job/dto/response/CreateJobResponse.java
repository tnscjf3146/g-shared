package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;

public record CreateJobResponse(Long jobId) {

    public static CreateJobResponse from(Job job) {
        return new CreateJobResponse(job.getId());
    }
}
