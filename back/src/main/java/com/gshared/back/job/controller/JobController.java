package com.gshared.back.job.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.response.JobDetailResponse;
import com.gshared.back.job.dto.response.JobPageResponse;
import com.gshared.back.job.service.JobService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.web.PageableDefault;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/jobs")
public class JobController {

    private final JobService jobService;

    @PostMapping
    public ResponseEntity<CreateJobResponse> create(
            @Valid @RequestBody CreateJobRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        CreateJobResponse response = jobService.create(userDetails.getId(), request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    @GetMapping("/{jobId}")
    public ResponseEntity<JobDetailResponse> getJob(
            @PathVariable Long jobId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobService.getJob(userDetails.getId(), jobId));
    }

    @GetMapping
    public ResponseEntity<JobPageResponse> getJobs(
            @RequestParam(required = false) JobStatus status,
            @PageableDefault(size = 10, sort = "id", direction = Sort.Direction.DESC) Pageable pageable,   // 최신순 고정 → 페이지 넘겨도 순서 안정
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobService.getJobs(userDetails.getId(), status, pageable));
    }

    @PostMapping("/{jobId}/cancel")
    public ResponseEntity<Void> cancelJob(
            @PathVariable Long jobId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        jobService.cancelJob(userDetails.getId(), jobId);
        return ResponseEntity.ok().build();
    }
}
