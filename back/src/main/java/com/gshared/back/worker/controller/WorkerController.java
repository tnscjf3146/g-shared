package com.gshared.back.worker.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.global.util.AuthorizationHeaderUtil;
import com.gshared.back.worker.dto.request.AuthenticateWorkerRequest;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.request.UpdateWorkerGpuRequest;
import com.gshared.back.worker.dto.response.AuthenticateWorkerResponse;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.dto.response.WorkerResponse;
import com.gshared.back.worker.service.WorkerService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 워커(공급자 PC) 관련 HTTP 창구.
 *
 * <p>인증 수단이 두 가지로 갈린다.
 * <ul>
 *   <li>{@code /register} — 사람이 로그인한 뒤 부르는 곳이라 <b>로그인 JWT</b>
 *   <li>{@code /authenticate}, {@code /{workerId}/gpu} — 사람 없이 워커 프로그램이
 *       부르는 곳이라 등록 때 발급한 <b>워커 토큰</b>
 * </ul>
 * 뒤 두 곳은 JWT 가 아니므로 SecurityConfig 에서 permitAll 로 두고(그래야 JWT 필터가
 * 막지 않는다) 실제 검증은 서비스가 워커 토큰으로 직접 한다. WebSocket 핸드셰이크와 같은 방식이다.
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/workers")
public class WorkerController {

    private final WorkerService workerService;

    @PostMapping("/register")
    public ResponseEntity<RegisterWorkerResponse> register(
            @Valid @RequestBody RegisterWorkerRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        RegisterWorkerResponse response = workerService.register(userDetails.getId(), request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    /**
     * 내 워커 목록. 공급자가 자기 PC 들의 상태를 보는 화면이 쓴다.
     * 사람이 로그인해서 보는 곳이므로 인증 수단은 워커 토큰이 아니라 로그인 JWT 다.
     */
    @GetMapping("/me")
    public ResponseEntity<List<WorkerResponse>> getMyWorkers(
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(workerService.getMyWorkers(userDetails.getId()));
    }

    /**
     * 워커 등록 해제. 돌려줄 내용이 없으므로 204 다.
     *
     * <p>여기도 사람이 부르는 곳이라 로그인 JWT 로 막는다 — 워커 토큰으로 자기 자신을
     * 지울 수 있게 두면, 토큰이 새어 나갔을 때 남의 PC 등록을 지워 버릴 수 있다.
     */
    @DeleteMapping("/{workerId}")
    public ResponseEntity<Void> delete(
            @PathVariable String workerId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        workerService.delete(userDetails.getId(), workerId);
        return ResponseEntity.noContent().build();
    }

    /**
     * 워커 재접속 확인. 워커 프로그램이 켜질 때마다 "저장해둔 토큰이 아직 유효한가"를 묻는다.
     * 조회만 하고 아무것도 바꾸지 않으며, 서버가 인정한 workerId 를 돌려준다.
     *
     * <p>워커는 응답을 받고 이렇게 갈라진다 — 그래서 상태코드를 임의로 바꾸면 안 된다.
     * <ul>
     *   <li>200 → 그대로 WebSocket 접속으로 진행
     *   <li>404 {@code WORKER_NOT_FOUND} → "내 등록이 사라졌다" 판단 후 재등록
     *   <li>401 → 인증 실패로 보고 멈춤 (재등록하지 않는다)
     *   <li>5xx → 서버 문제로 보고 나중에 재시도 (등록 상태는 건드리지 않음)
     * </ul>
     *
     * <p>본문의 workerId 는 인증 수단이 아니라 "내가 이 워커라고 알고 있다"는 주장이다.
     * 인증은 헤더의 워커 토큰으로 하고, 서비스가 그 주장과 토큰이 같은 워커를 가리키는지 대조한다.
     */
    @PostMapping("/authenticate")
    public ResponseEntity<AuthenticateWorkerResponse> authenticate(
            @Valid @RequestBody AuthenticateWorkerRequest request,
            HttpServletRequest httpRequest
    ) {
        String workerId = workerService.authenticate(
                AuthorizationHeaderUtil.extract(httpRequest), request.workerId());
        return ResponseEntity.ok(new AuthenticateWorkerResponse(workerId));
    }

    /**
     * GPU 정보 갱신. 그래픽카드를 바꿔 꽂았거나 벤치마크를 다시 측정했을 때 워커가 알려온다.
     * 등록(register) 때 받은 하드웨어 스냅샷을 덮어쓰는 것이며, 돌려줄 내용이 없으므로 204 다.
     *
     * <p>경로의 workerId 와 헤더 토큰의 주인이 같아야 한다. 다르면 404 —
     * 남의 워커 스펙을 고칠 수 있으면 배정 판단(어느 GPU가 이 작업을 감당하는가)이 통째로 흔들린다.
     *
     * <p>여기서 갱신되는 vramMb·benchmarkTokensPerSec 은 AI Scheduler 가 실행시간을 예측하는
     * 입력이다. 그래서 요청 DTO 가 네 값을 모두 필수·양수로 막는다(빈 값이면 400).
     */
    @PutMapping("/{workerId}/gpu")
    public ResponseEntity<Void> updateGpu(
            @PathVariable String workerId,
            @Valid @RequestBody UpdateWorkerGpuRequest request,
            HttpServletRequest httpRequest
    ) {
        workerService.updateGpu(AuthorizationHeaderUtil.extract(httpRequest), workerId, request);
        return ResponseEntity.noContent().build();
    }
}
