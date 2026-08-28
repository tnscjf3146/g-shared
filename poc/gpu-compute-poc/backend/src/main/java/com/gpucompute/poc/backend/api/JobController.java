package com.gpucompute.poc.backend.api;

import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.gpucompute.poc.backend.job.JobConflictException;
import com.gpucompute.poc.backend.job.JobNotFoundException;
import com.gpucompute.poc.backend.job.JobService;

@RestController
@RequestMapping("/api/jobs")
public class JobController {
    private final JobService jobs;

    public JobController(JobService jobs) {
        this.jobs = jobs;
    }

    @PostMapping
    public ResponseEntity<JobResponse> dispatch(@RequestBody JobRequest request) {
        return ResponseEntity.accepted()
            .body(JobResponse.from(jobs.dispatch(request.workerId(), request.workload())));
    }

    @GetMapping("/{jobId}")
    public JobResponse find(@PathVariable UUID jobId) {
        return JobResponse.from(jobs.findById(jobId));
    }

    @ExceptionHandler(JobNotFoundException.class)
    ResponseEntity<Void> notFound() {
        return ResponseEntity.notFound().build();
    }

    @ExceptionHandler(JobConflictException.class)
    ResponseEntity<Void> conflict() {
        return ResponseEntity.status(HttpStatus.CONFLICT).build();
    }

    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<Void> badRequest() {
        return ResponseEntity.badRequest().build();
    }
}
