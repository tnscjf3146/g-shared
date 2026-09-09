package com.gshared.back.worker.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.request.UpdateWorkerGpuRequest;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.domain.WorkerStatus;
import com.gshared.back.worker.exception.WorkerErrorCode;
import com.gshared.back.worker.repository.WorkerRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class WorkerService {

    private final WorkerRepository workerRepository;
    private final UserRepository userRepository;

    /**
     * 로그인한 공급자가 자기 PC를 등록한다.
     * - workerId  : 공개 식별자 (WebSocket 메시지에 실림)
     * - workerToken: 비밀 토큰 (WebSocket 연결 인증용)
     */
    @Transactional
    public RegisterWorkerResponse register(Long ownerId, RegisterWorkerRequest request) {
        User owner = userRepository.findById(ownerId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));

        String workerId = "worker-" + UUID.randomUUID();
        String rawToken = UUID.randomUUID().toString();

        // DB 에는 해시만 저장 (DB 유출 시 토큰으로 위장 접속 못 하게). 원문은 이 응답에서 딱 한 번 노출.
        Worker worker = Worker.register(
                workerId, owner, sha256(rawToken),
                request.deviceName(), request.osVersion(), request.gpuModel(),
                request.vramMb(), request.driverVersion(), request.benchmarkTokensPerSec()
        );
        workerRepository.save(worker);

        return new RegisterWorkerResponse(workerId, rawToken);
    }

    /**
     * WebSocket 핸드셰이크: 워커 토큰(원문)으로 workerId 조회. 유효하지 않으면 401.
     *
     * <p>아래 {@link #findOwnWorker}(HTTP 창구용)와 조회 방식은 같지만 실패 코드가 다르다.
     * 핸드셰이크에서는 연결을 거절하는 것이 전부라 401 이 맞고,
     * HTTP 창구에서는 워커가 그 코드를 보고 재등록 여부를 정하므로 404 여야 한다.
     * 그래서 하나로 합치지 않고 둘로 둔다.
     */
    public String resolveWorkerIdByToken(String rawToken) {
        return workerRepository.findByWorkerToken(sha256(rawToken))
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.INVALID_WORKER_TOKEN))
                .getId();
    }

    /**
     * 워커 재접속 확인: 저장해둔 워커 토큰이 아직 유효한지 묻는 요청. 아무것도 바꾸지 않는다.
     * 워커는 켜질 때마다 이걸 물어보고, 답에 따라 그대로 접속할지 재등록할지 정한다.
     *
     * @return 서버가 인정한 workerId (워커 클라이언트가 자기 값과 다시 대조한다)
     */
    public String authenticate(String rawToken, String workerId) {
        return findOwnWorker(rawToken, workerId).getId();
    }

    /**
     * GPU 정보 갱신: 그래픽카드 교체·드라이버 갱신·벤치마크 재측정 결과를 반영한다.
     * 하드웨어 정보만 바꾸며 ONLINE/RUNNING 같은 실시간 상태는 건드리지 않는다.
     */
    @Transactional
    public void updateGpu(String rawToken, String workerId, UpdateWorkerGpuRequest request) {
        findOwnWorker(rawToken, workerId).updateGpu(
                request.gpuModel(), request.vramMb(),
                request.driverVersion(), request.benchmarkTokensPerSec());
    }

    /**
     * 워커 토큰이 가리키는 워커를 찾고, 요청이 주장하는 workerId 와 같은 워커인지 확인한다.
     *
     * <p>토큰에 해당하는 워커가 없거나 다른 워커의 토큰이면 WORKER_NOT_FOUND(404) 다 —
     * 워커 클라이언트는 이 코드를 "내 등록이 사라졌다"로 읽고 재등록으로 넘어간다.
     * 401 을 주면 재등록 없이 그냥 멈춰버리므로, 여기서는 404 여야 한다.
     * (남의 워커를 지목한 경우도 존재 여부를 흘리지 않도록 같은 404 로 묶는다)
     *
     * <p>반면 헤더 자체가 없으면 인증 시도조차 없었던 것이므로 401 이다.
     */
    private Worker findOwnWorker(String rawToken, String workerId) {
        if (rawToken == null || rawToken.isBlank()) {
            throw new BusinessException(WorkerErrorCode.INVALID_WORKER_TOKEN);
        }
        Worker worker = workerRepository.findByWorkerToken(sha256(rawToken))
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.WORKER_NOT_FOUND));
        if (!worker.getId().equals(workerId)) {
            throw new BusinessException(WorkerErrorCode.WORKER_NOT_FOUND);
        }
        return worker;
    }

    /** 랜덤 UUID 토큰이라 느린 bcrypt 불필요 — 조회 가능한 SHA-256 이면 충분. */
    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 미지원 JVM", e);
        }
    }

    /** REGISTER 수신 → ONLINE. */
    @Transactional
    public void markOnline(String workerId, LocalDateTime now) {
        findWorker(workerId).goOnline(now);
    }

    /** HEARTBEAT 수신 → 살아있음 시각 갱신. */
    @Transactional
    public void touchHeartbeat(String workerId, LocalDateTime now) {
        findWorker(workerId).touchHeartbeat(now);
    }

    /** 연결 종료 → OFFLINE. */
    @Transactional
    public void markOffline(String workerId) {
        findWorker(workerId).goOffline();
    }

    /**
     * 하트비트 타임아웃 스위퍼: 기준 시각 이전에 마지막 하트비트를 보낸 (OFFLINE 아닌) 워커들을
     * OFFLINE 처리하고 그 id 목록을 반환한다. (호출자가 세션도 정리)
     */
    @Transactional
    public java.util.List<String> sweepStaleWorkers(LocalDateTime threshold) {
        return workerRepository.findByStatusNotAndLastHeartbeatAtBefore(WorkerStatus.OFFLINE, threshold)
                .stream()
                .peek(Worker::goOffline)
                .map(Worker::getId)
                .toList();
    }

    private Worker findWorker(String workerId) {
        return workerRepository.findById(workerId)
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.WORKER_NOT_FOUND));
    }
}
