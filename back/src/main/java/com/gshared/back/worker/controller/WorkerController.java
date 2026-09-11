package com.gshared.back.worker.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.global.util.AuthorizationHeaderUtil;
import com.gshared.back.worker.dto.request.AuthenticateWorkerRequest;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.request.UpdateBenchmarkRequest;
import com.gshared.back.worker.dto.request.UpdateOperatorRequest;
import com.gshared.back.worker.dto.response.AuthenticateWorkerResponse;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.dto.response.UpdateBenchmarkResponse;
import com.gshared.back.worker.dto.response.UpdateOperatorResponse;
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
 * 워커(공급자 PC) 관련 HTTP 창구. (System ↔ Backend 연동 규격 v1 §3~§6)
 *
 * <p>인증 수단이 두 가지로 갈린다.
 * <ul>
 *   <li>{@code /register}, {@code /me}, {@code DELETE /{workerId}} — 사람이 로그인한 뒤 부르는 곳이라 <b>로그인 JWT</b>
 *   <li>{@code /authenticate}, {@code /{workerId}/operator}, {@code /{workerId}/benchmark} — 사람 없이 워커 프로그램이
 *       부르는 곳이라 등록 때 발급한 <b>워커 토큰</b>
 * </ul>
 * 뒤 세 곳은 JWT 가 아니므로 SecurityConfig 에서 permitAll 로 두고(그래야 JWT 필터가
 * 막지 않는다) 실제 검증은 서비스가 워커 토큰으로 직접 한다. WebSocket 핸드셰이크와 같은 방식이다.
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/workers")
public class WorkerController {

    private final WorkerService workerService;

    /**
     * 신규 워커 등록. User Access Token 은 여기서만 쓰이고, 워커는 등록 후 그 토큰을 버린다.
     * 응답의 workerToken 원문은 이 한 번만 노출된다.
     */
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
     * 워커 재접속 확인 (§4). 워커 프로그램이 켜질 때마다 "저장해둔 토큰이 아직 유효한가"를 묻는다.
     * 조회만 하고 아무것도 바꾸지 않는다.
     *
     * <p>규격상 토큰은 본문에 실린다. 응답은 200 이면 그대로 WebSocket 접속, 401/403 이면 멈춘다 —
     * 인증 실패는 재연결로 풀리지 않으므로 워커가 무한 재시도하지 않는다.
     */
    @PostMapping("/authenticate")
    public ResponseEntity<AuthenticateWorkerResponse> authenticate(
            @Valid @RequestBody AuthenticateWorkerRequest request
    ) {
        String workerId = workerService.authenticate(request.workerId(), request.workerToken());
        return ResponseEntity.ok(new AuthenticateWorkerResponse(workerId, true));
    }

    /**
     * operator 주소 변경 (§5). 바꾼 뒤에는 provider 가 온체인 setOperator(operator, true) 로 승인해야
     * 이 워커가 acceptJob 을 부를 수 있다 — 그 승인은 Backend 가 아니라 System 이 확인한다.
     */
    @PutMapping("/{workerId}/operator")
    public ResponseEntity<UpdateOperatorResponse> updateOperator(
            @PathVariable String workerId,
            @Valid @RequestBody UpdateOperatorRequest request,
            HttpServletRequest httpRequest
    ) {
        String operatorAddress = workerService.updateOperator(
                AuthorizationHeaderUtil.extract(httpRequest), workerId, request.operatorAddress());
        return ResponseEntity.ok(new UpdateOperatorResponse(workerId, operatorAddress, true));
    }

    /**
     * GPU 벤치마크 갱신 (§6). 그래픽카드를 바꿔 꽂았거나 벤치마크를 다시 측정했을 때 워커가 알려온다.
     *
     * <p>경로의 workerId 와 헤더 토큰의 주인이 같아야 한다. 다르면 403 —
     * 남의 워커 스펙을 고칠 수 있으면 배정 판단(어느 GPU가 이 작업을 감당하는가)이 통째로 흔들린다.
     */
    @PutMapping("/{workerId}/benchmark")
    public ResponseEntity<UpdateBenchmarkResponse> updateBenchmark(
            @PathVariable String workerId,
            @Valid @RequestBody UpdateBenchmarkRequest request,
            HttpServletRequest httpRequest
    ) {
        workerService.updateBenchmark(AuthorizationHeaderUtil.extract(httpRequest), workerId, request);
        return ResponseEntity.ok(new UpdateBenchmarkResponse(workerId, true));
    }
}
