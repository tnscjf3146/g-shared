package com.gshared.back.worker.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
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

    /** WebSocket 핸드셰이크: 워커 토큰(원문)으로 workerId 조회. 유효하지 않으면 401. */
    public String resolveWorkerIdByToken(String rawToken) {
        return workerRepository.findByWorkerToken(sha256(rawToken))
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.INVALID_WORKER_TOKEN))
                .getId();
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
