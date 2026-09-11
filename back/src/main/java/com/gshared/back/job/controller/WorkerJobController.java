package com.gshared.back.job.controller;

import com.gshared.back.global.util.AuthorizationHeaderUtil;
import com.gshared.back.job.dto.request.ResultUploadCompleteRequest;
import com.gshared.back.job.dto.request.ResultUploadRequest;
import com.gshared.back.job.dto.response.ExecutionContextResponse;
import com.gshared.back.job.dto.response.ResultUploadCompleteResponse;
import com.gshared.back.job.dto.response.ResultUploadResponse;
import com.gshared.back.job.service.WorkerJobService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * 워커(System)가 부르는 Job 창구 (연동 규격 §11·§17·§18, 경로는 §35).
 *
 * <p>사용자용 {@code /api/jobs/**} 와 달리 인증은 로그인 JWT 가 아니라 <b>워커 토큰</b>이다.
 * SecurityConfig 가 {@code /api/worker/**} 를 permitAll 로 두고, 서비스가 헤더의 토큰으로 워커를 찾은 뒤
 * "그 워커에게 배정된 Job 인가"를 확인한다.
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/worker/jobs")
public class WorkerJobController {

    private final WorkerJobService workerJobService;

    /** JOB_ASSIGN 을 받은 워커가 실행에 필요한 전부(모델·이미지·데이터셋·설정·에스크로 대조값)를 조회한다. */
    @GetMapping("/{jobId}/execution-context")
    public ResponseEntity<ExecutionContextResponse> getExecutionContext(
            @PathVariable Long jobId,
            HttpServletRequest httpRequest
    ) {
        return ResponseEntity.ok(workerJobService.getExecutionContext(
                AuthorizationHeaderUtil.extract(httpRequest), jobId));
    }

    /** 결과물(LoRA 어댑터 tar) 을 올릴 서명 URL 발급. */
    @PostMapping("/{jobId}/result-upload")
    public ResponseEntity<ResultUploadResponse> createResultUpload(
            @PathVariable Long jobId,
            @Valid @RequestBody ResultUploadRequest request,
            HttpServletRequest httpRequest
    ) {
        return ResponseEntity.ok(workerJobService.createResultUpload(
                AuthorizationHeaderUtil.extract(httpRequest), jobId, request));
    }

    /** 업로드가 끝났다는 알림. Backend 가 실제 오브젝트를 확인한 뒤에야 verified 다. */
    @PostMapping("/{jobId}/result-upload/complete")
    public ResponseEntity<ResultUploadCompleteResponse> completeResultUpload(
            @PathVariable Long jobId,
            @Valid @RequestBody ResultUploadCompleteRequest request,
            HttpServletRequest httpRequest
    ) {
        return ResponseEntity.ok(workerJobService.completeResultUpload(
                AuthorizationHeaderUtil.extract(httpRequest), jobId, request));
    }
}
