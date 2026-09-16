package com.gshared.back.worker.service;

import com.gshared.back.global.blockchain.EscrowProperties;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.wallet.WalletAuthProperties;
import com.gshared.back.global.wallet.WalletAddressNormalizer;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.request.UpdateBenchmarkRequest;
import com.gshared.back.worker.dto.response.OperatorStatusResponse;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.dto.response.WorkerResponse;
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
import java.util.List;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class WorkerService {

    private final WorkerRepository workerRepository;
    private final UserRepository userRepository;
    private final WalletAddressNormalizer addressNormalizer;
    private final OperatorApprovalChecker approvalChecker;
    private final WalletAuthProperties walletAuthProperties;
    private final EscrowProperties escrowProperties;

    /**
     * 로그인한 공급자가 자기 PC를 등록한다 (규격 §3).
     * - workerId  : 공개 식별자 (WebSocket 메시지에 실림)
     * - workerToken: 비밀 토큰 (WebSocket·워커 전용 REST 인증용)
     * User 1 : N Worker — 한 사람이 PC 여러 대를 붙일 수 있다.
     */
    @Transactional
    public RegisterWorkerResponse register(Long ownerId, RegisterWorkerRequest request) {
        User owner = userRepository.findById(ownerId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));

        String workerId = "worker-" + UUID.randomUUID();
        String rawToken = UUID.randomUUID().toString();
        String operatorAddress = normalizeOperator(request.operatorAddress());

        // DB 에는 해시만 저장 (DB 유출 시 토큰으로 위장 접속 못 하게). 원문은 이 응답에서 딱 한 번 노출.
        Worker worker = Worker.register(
                workerId, owner, sha256(rawToken), operatorAddress, request.clientVersion(),
                request.system().os(), request.system().wslAvailable(),
                request.gpu().gpuModel(), request.gpu().vramMb(),
                request.benchmark().benchmarkTokensPerSec(), request.benchmark().benchmarkVersion()
        );
        workerRepository.save(worker);

        return new RegisterWorkerResponse(workerId, rawToken, operatorAddress);
    }

    /**
     * WebSocket 핸드셰이크: 워커 토큰(원문)으로 workerId 조회. 유효하지 않으면 401.
     */
    public String resolveWorkerIdByToken(String rawToken) {
        return authenticateByToken(rawToken).getId();
    }

    /**
     * 워커 전용 REST(execution-context·result-upload) 인증: 헤더의 워커 토큰이 가리키는 워커.
     * 토큰이 없거나 어떤 워커도 가리키지 않으면 401 — 규격 §4 대로 워커는 이 코드에 재시도하지 않는다.
     */
    public Worker authenticateByToken(String rawToken) {
        if (rawToken == null || rawToken.isBlank()) {
            throw new BusinessException(WorkerErrorCode.INVALID_WORKER_TOKEN);
        }
        return workerRepository.findByWorkerToken(sha256(rawToken))
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.INVALID_WORKER_TOKEN));
    }

    /**
     * 워커 재접속 확인 (규격 §4): 저장해둔 workerId·workerToken 이 아직 유효한지. 아무것도 바꾸지 않는다.
     * 토큰이 무효면 401, 토큰은 맞는데 다른 워커를 주장하면 403.
     *
     * @return 서버가 인정한 workerId
     */
    public String authenticate(String workerId, String rawToken) {
        return findOwnWorker(rawToken, workerId).getId();
    }

    /**
     * operator 주소 변경 (규격 §5). 주소만 바꿔 저장한다 — 온체인 setOperator 승인은 provider 몫이다.
     * 바뀐 주소는 승인 전이므로 이후 {@link #getOperatorStatus} 는 NOT_APPROVED 를 돌려주고,
     * 매칭도 배정 직전에 isOperator 로 다시 확인한다.
     *
     * @return 저장된(정규화된) 주소
     */
    @Transactional
    public String updateOperator(String rawToken, String workerId, String operatorAddress) {
        String normalized = normalizeOperator(operatorAddress);
        findOwnWorker(rawToken, workerId).updateOperator(normalized);
        return normalized;
    }

    /**
     * GPU 벤치마크 갱신 (규격 §6): 그래픽카드 교체·벤치마크 재측정 결과를 반영한다.
     * 하드웨어 정보만 바꾸며 ONLINE/RUNNING 같은 실시간 상태는 건드리지 않는다.
     */
    @Transactional
    public void updateBenchmark(String rawToken, String workerId, UpdateBenchmarkRequest request) {
        findOwnWorker(rawToken, workerId).updateBenchmark(
                request.gpuModel(), request.vramMb(),
                request.benchmarkTokensPerSec(), request.benchmarkVersion());
    }

    /**
     * 내가 등록한 워커 전부. 한 대도 없으면 빈 목록이다 — 없는 것은 오류가 아니라
     * "아직 안 붙였다"는 정상 상태이고, 화면은 그때 설치 안내를 보여준다.
     */
    public List<WorkerResponse> getMyWorkers(Long ownerId) {
        return workerRepository.findMineWithOwner(ownerId).stream()
                .map(w -> WorkerResponse.from(w, approvalChecker.availabilityOf(w)))
                .toList();
    }

    /**
     * 이 워커의 operator 키를 공급자가 온체인에서 승인했는가 — 지금 체인에 물어본 그대로(캐시 없음).
     * 주인만 볼 수 있고, 남의 것을 지목하면 존재 여부를 흘리지 않도록 404 다.
     *
     * <p>확인하지 못한 경우(체인 미설정·노드 무응답)도 오류가 아니라 UNVERIFIED 다 — 승인을 안 한 것과
     * 모르는 것은 다르고, 화면은 그 차이로 "승인하세요"와 "잠시 후 다시 확인하세요"를 가른다.
     */
    public OperatorStatusResponse getOperatorStatus(Long ownerId, String workerId) {
        Worker worker = workerRepository.findByIdAndOwnerId(workerId, ownerId)
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.WORKER_NOT_FOUND));
        return OperatorStatusResponse.of(worker, approvalChecker.approvalOf(worker),
                walletAuthProperties.chainId(), escrowProperties.contractAddressOrNull());
    }

    /**
     * 워커 등록 해제. 주인만 지울 수 있고, 남의 것을 지목하면 존재 여부를 흘리지 않도록 404 다.
     *
     * <p>일을 맡고 있는 워커는 지우지 않는다. 지워 버리면 그 Job 은 결과를 받을 상대가
     * 사라진 채 RUNNING 에 남고, 되돌릴 방법이 없다. 취소를 먼저 하도록 409 로 막는다.
     *
     * <p>지운 뒤 워커 프로그램은 다음 인증에서 401 을 받고 멈춘다 — 사람이 다시 로그인해 등록한다.
     */
    @Transactional
    public void delete(Long ownerId, String workerId) {
        Worker worker = workerRepository.findByIdAndOwnerId(workerId, ownerId)
                .orElseThrow(() -> new BusinessException(WorkerErrorCode.WORKER_NOT_FOUND));
        if (worker.getStatus() == WorkerStatus.RUNNING || worker.getCurrentJobId() != null) {
            throw new BusinessException(WorkerErrorCode.WORKER_BUSY);
        }
        workerRepository.delete(worker);
    }

    /**
     * 워커 토큰이 가리키는 워커를 찾고, 요청이 주장하는 workerId 와 같은 워커인지 확인한다.
     * 토큰 무효 → 401, 다른 워커 주장 → 403 (규격 §4).
     */
    private Worker findOwnWorker(String rawToken, String workerId) {
        Worker worker = authenticateByToken(rawToken);
        if (!worker.getId().equals(workerId)) {
            throw new BusinessException(WorkerErrorCode.WORKER_FORBIDDEN);
        }
        return worker;
    }

    private String normalizeOperator(String operatorAddress) {
        return addressNormalizer.normalize(operatorAddress, WorkerErrorCode.INVALID_OPERATOR_ADDRESS);
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
    public List<String> sweepStaleWorkers(LocalDateTime threshold) {
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
