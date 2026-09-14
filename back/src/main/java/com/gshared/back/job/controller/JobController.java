package com.gshared.back.job.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.request.EstimateJobRequest;
import com.gshared.back.job.dto.request.FundJobRequest;
import com.gshared.back.job.dto.request.JobSpecRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.response.EstimateResponse;
import com.gshared.back.job.dto.response.FundJobResponse;
import com.gshared.back.job.dto.response.GpuOptionResponse;
import com.gshared.back.job.dto.response.JobDetailResponse;
import com.gshared.back.job.dto.response.JobPageResponse;
import com.gshared.back.job.service.JobEstimateService;
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

import java.util.List;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/jobs")
public class JobController {

    private final JobService jobService;
    private final JobEstimateService jobEstimateService;

    @PostMapping
    public ResponseEntity<CreateJobResponse> create(
            @Valid @RequestBody CreateJobRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        CreateJobResponse response = jobService.create(userDetails.getId(), request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    /**
     * 이 명세를 감당할 GPU 등급들과 적합도. 아무것도 저장하지 않는다.
     *
     * <p>조회인데 POST 인 것은 명세(데이터셋 분석값·학습 설정)를 본문으로 보내야 하기 때문이다.
     * 질의 문자열에 담기에는 크고, 그렇다고 파일을 올릴 단계도 아니다.
     */
    @PostMapping("/gpu-options")
    public ResponseEntity<List<GpuOptionResponse>> getGpuOptions(
            @Valid @RequestBody JobSpecRequest request
    ) {
        return ResponseEntity.ok(jobEstimateService.getGpuOptions(request));
    }

    /**
     * 고른 GPU 로 예상 시간·비용을 계산한다. 여기까지도 저장하지 않는다 —
     * 사용자가 설정을 만질 때마다 불리는 곳이라, 행을 만들면 버려진 주문서가 쌓인다.
     */
    @PostMapping("/estimate")
    public ResponseEntity<EstimateResponse> estimate(
            @Valid @RequestBody EstimateJobRequest request
    ) {
        return ResponseEntity.ok(jobEstimateService.estimate(request));
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

    /**
     * 예치 확인. 의뢰자가 createAndFundJob() 을 보낸 트랜잭션 해시를 실어 보내면 Backend 가 영수증을 읽어
     * JobFunded 이벤트를 자기 기록(externalRef·client·provider·price)과 대조하고, 맞을 때만 FUNDED 로 바꾼다.
     * 영수증이 아직 없거나 노드가 답하지 않으면 202 + PENDING — 프런트는 같은 txHash 로 다시 물으면 된다.
     */
    @PostMapping("/{jobId}/fund")
    public ResponseEntity<FundJobResponse> fundJob(
            @PathVariable Long jobId,
            @Valid @RequestBody FundJobRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        FundJobResponse response = jobService.fundJob(userDetails.getId(), jobId, request.txHash(), request.escrowJobId());
        HttpStatus status = FundJobResponse.PENDING.equals(response.status()) ? HttpStatus.ACCEPTED : HttpStatus.OK;
        return ResponseEntity.status(status).body(response);
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
