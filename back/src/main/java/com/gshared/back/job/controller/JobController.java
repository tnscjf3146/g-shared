package com.gshared.back.job.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.job.dto.request.AdapterUploadCompleteRequest;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.request.DatasetUploadCompleteRequest;
import com.gshared.back.job.dto.request.EstimateJobRequest;
import com.gshared.back.job.dto.request.FundJobRequest;
import com.gshared.back.job.dto.request.JobSpecRequest;
import com.gshared.back.job.dto.request.RenameAdapterRequest;
import com.gshared.back.job.dto.response.AdapterUploadCompleteResponse;
import com.gshared.back.job.dto.response.AdapterUploadResponse;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.dto.response.DatasetUploadCompleteResponse;
import com.gshared.back.job.dto.response.DatasetUploadResponse;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.response.EstimateResponse;
import com.gshared.back.job.dto.response.FundJobResponse;
import com.gshared.back.job.dto.response.GpuOptionResponse;
import com.gshared.back.job.dto.response.JobDetailResponse;
import com.gshared.back.job.dto.response.JobPageResponse;
import com.gshared.back.job.dto.response.JobResultVerificationResponse;
import com.gshared.back.job.service.JobAdapterService;
import com.gshared.back.job.service.JobDatasetService;
import com.gshared.back.job.service.JobEstimateService;
import com.gshared.back.job.service.JobResultVerificationService;
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
    private final JobResultVerificationService jobResultVerificationService;
    private final JobEstimateService jobEstimateService;
    private final JobDatasetService jobDatasetService;
    private final JobAdapterService jobAdapterService;

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

    /**
     * 학습 결과 검증 데이터(loss curve·LoRA weight 검증·hash) — Job 상세 '학습 검증' 탭 전용.
     * COMPLETED 가 아니거나 검증 데이터가 없는 legacy 결과는 에러가 아니라 available=false 로 답한다.
     */
    @GetMapping("/{jobId}/result-verification")
    public ResponseEntity<JobResultVerificationResponse> getResultVerification(
            @PathVariable Long jobId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobResultVerificationService.getVerification(userDetails.getId(), jobId));
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
     * 데이터셋 원본을 올릴 서명 URL 발급. Job 생성 뒤·예치 전(CREATED)에만.
     * 파일 본문은 여기를 지나지 않는다 — 프런트가 uploadUrl 에 직접 PUT 한다.
     */
    @PostMapping("/{jobId}/dataset-upload")
    public ResponseEntity<DatasetUploadResponse> createDatasetUpload(
            @PathVariable Long jobId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobDatasetService.createUpload(userDetails.getId(), jobId));
    }

    /**
     * 업로드가 끝났다는 알림. Backend 가 실제 오브젝트를 등록된 Dataset 메타와 대조한 뒤에야 verified 이고, 그래야 /fund 가 열린다.
     * 본문은 선택 — 프런트는 보내지 않아도 되고, 보내면 그 신고값과도 대조한다.
     */
    @PostMapping("/{jobId}/dataset-upload/complete")
    public ResponseEntity<DatasetUploadCompleteResponse> completeDatasetUpload(
            @PathVariable Long jobId,
            @Valid @RequestBody(required = false) DatasetUploadCompleteRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobDatasetService.completeUpload(userDetails.getId(), jobId,
                request != null ? request : DatasetUploadCompleteRequest.EMPTY));
    }

    /**
     * 입력 어댑터(.tar)를 올릴 서명 URL 발급. 데이터셋과 같은 흐름·같은 조건(본인·CREATED)이고,
     * 주문 때 어댑터를 쓰겠다고 한 Job 만 받는다. "이어서 학습"은 부모 결과물을 쓰므로 여기를 지나지 않는다.
     */
    @PostMapping("/{jobId}/adapter-upload")
    public ResponseEntity<AdapterUploadResponse> createAdapterUpload(
            @PathVariable Long jobId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobAdapterService.createUpload(userDetails.getId(), jobId));
    }

    /**
     * 어댑터 업로드가 끝났다는 알림. Backend 가 실제 오브젝트의 크기·해시와 tar 형식을 확인한 뒤에야 verified 이고,
     * 그래야 /fund 가 열린다. 본문은 선택 — 보내면 그 신고값과도 대조한다.
     */
    @PostMapping("/{jobId}/adapter-upload/complete")
    public ResponseEntity<AdapterUploadCompleteResponse> completeAdapterUpload(
            @PathVariable Long jobId,
            @Valid @RequestBody(required = false) AdapterUploadCompleteRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobAdapterService.completeUpload(userDetails.getId(), jobId,
                request != null ? request : AdapterUploadCompleteRequest.EMPTY));
    }

    /**
     * 결과 어댑터 이름 변경. 이름은 내려받을 파일 이름이라 결과를 받기 직전에 고치는 일이 잦다 —
     * 파일도 계보도 건드리지 않으므로 상태 제한을 두지 않는다.
     */
    @PatchMapping("/{jobId}/adapter-name")
    public ResponseEntity<JobDetailResponse> renameAdapter(
            @PathVariable Long jobId,
            @Valid @RequestBody RenameAdapterRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(jobService.renameAdapter(userDetails.getId(), jobId, request.adapterName()));
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
