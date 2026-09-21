package com.gshared.back.worker.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import com.gshared.back.user.domain.User;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Getter
@Table(name = "workers")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Worker extends BaseTimeEntity {

    @Id
    @Column(name = "worker_id", length = 64)
    private String id;                 // "worker-{uuid}" — WebSocket 메시지에 실려 다니는 식별자

    // 공급자(주인). 정산 시 이 회원의 지갑(provider)으로 토큰 지급.
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "owner_id", nullable = false)
    private User owner;

    @Column(nullable = false, unique = true)   // DB: uk_workers_token (V3 마이그레이션)
    private String workerToken;        // 워커 토큰의 SHA-256 해시 (원문은 발급 시 1회만 노출)

    /**
     * 온체인 트랜잭션(acceptJob·submitResult·abortJob)에 서명하는 주소. 공개 주소만 저장한다 —
     * 개인키는 Worker PC(DPAPI)에만 있고 Backend 로 넘어오지 않는다.
     * 정산 주소(owner.walletAddress = provider)와는 다른 키다: provider 가 setOperator 로 승인해야 쓸 수 있다.
     */
    @Column(length = 42)
    private String operatorAddress;
    @Column(length = 32)
    private String clientVersion;      // G-shared.exe 버전 — 규격 불일치 추적용

    // 하드웨어 스펙 (register 시점 스냅샷)
    private String deviceName;         // 구 규격 필드 — 새 등록에서는 채우지 않는다
    private String osVersion;          // system.os
    private Boolean wslAvailable;      // system.wslAvailable — Docker(WSL2) 실행 가능 여부
    private String gpuModel;
    private Integer vramMb;
    private String driverVersion;      // 구 규격 필드 — 새 등록에서는 채우지 않는다
    private Double benchmarkTokensPerSec;
    /**
     * 벤치마크 중 실제로 쓴 VRAM 최고치(MiB). vramMb 는 카드의 총량이고, 이쪽이 학습에 쓸 수 있는 양에 가깝다.
     * 구버전 exe 는 보내지 않으므로 NULL 일 수 있다 — Scheduler 로는 있으면 싣고 없으면 null 로 간다 (V21).
     */
    private Integer benchmarkPeakVramMb;
    @Column(length = 64)
    private String benchmarkVersion;   // 어떤 벤치마크로 잰 값인가 — 버전이 다르면 수치를 비교할 수 없다

    // 실시간 상태 (WebSocket 단계에서 갱신)
    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private WorkerStatus status;

    private Long currentJobId;        // jobs.job_id 와 타입 일치 (BIGINT)
    private LocalDateTime lastHeartbeatAt;

    private Worker(String id, User owner, String workerToken, String operatorAddress, String clientVersion,
                   String osVersion, Boolean wslAvailable, String gpuModel, Integer vramMb,
                   Double benchmarkTokensPerSec, Integer benchmarkPeakVramMb, String benchmarkVersion) {
        this.id = id;
        this.owner = owner;
        this.workerToken = workerToken;
        this.operatorAddress = operatorAddress;
        this.clientVersion = clientVersion;
        this.osVersion = osVersion;
        this.wslAvailable = wslAvailable;
        this.gpuModel = gpuModel;
        this.vramMb = vramMb;
        this.benchmarkTokensPerSec = benchmarkTokensPerSec;
        this.benchmarkPeakVramMb = benchmarkPeakVramMb;
        this.benchmarkVersion = benchmarkVersion;
        this.status = WorkerStatus.OFFLINE;   // 등록 직후엔 아직 연결 전
    }

    /**
     * 규격 v1 등록. operatorAddress 는 정규화(소문자)된 값만 넘어온다는 전제다.
     * benchmarkPeakVramMb 는 선택 — 구버전 exe 는 보내지 않으니 null 그대로 둔다.
     */
    public static Worker register(String id, User owner, String workerToken, String operatorAddress,
                                  String clientVersion, String osVersion, Boolean wslAvailable,
                                  String gpuModel, Integer vramMb,
                                  Double benchmarkTokensPerSec, Integer benchmarkPeakVramMb,
                                  String benchmarkVersion) {
        return new Worker(id, owner, workerToken, operatorAddress, clientVersion, osVersion, wslAvailable,
                gpuModel, vramMb, benchmarkTokensPerSec, benchmarkPeakVramMb, benchmarkVersion);
    }

    /**
     * operator 주소 교체. 이후 provider 가 온체인 setOperator(operator, true) 로 승인해야
     * 이 워커가 다시 후보에 오른다 — Backend 는 주소만 바꿔 두고 승인 여부는 System 이 확인한다.
     */
    public void updateOperator(String operatorAddress) {
        this.operatorAddress = operatorAddress;
    }

    /**
     * GPU·벤치마크 갱신. 그래픽카드 교체·벤치마크 재측정 시 워커가 다시 알려준다.
     * 신원(id·토큰·주인)과 실시간 상태(status·currentJobId)는 건드리지 않는다 —
     * 여기서 바뀌는 것은 "이 워커가 무엇을 제공하는가" 뿐이다.
     */
    public void updateBenchmark(String gpuModel, Integer vramMb,
                                Double benchmarkTokensPerSec, Integer benchmarkPeakVramMb,
                                String benchmarkVersion) {
        this.gpuModel = gpuModel;
        this.vramMb = vramMb;
        this.benchmarkTokensPerSec = benchmarkTokensPerSec;
        // 구버전 exe 가 다시 재면 null 이 온다 — 이전 측정이 아직 유효하다는 뜻이 아니라(카드가 바뀌었을 수 있다) 그대로 덮는다
        this.benchmarkPeakVramMb = benchmarkPeakVramMb;
        this.benchmarkVersion = benchmarkVersion;
    }

    /** REGISTER 수신: 연결됨 + 작업 없음 상태로. */
    public void goOnline(LocalDateTime now) {
        this.status = WorkerStatus.ONLINE;
        this.currentJobId = null;
        this.lastHeartbeatAt = now;
    }

    /**
     * HEARTBEAT 수신: 살아있음 시각 갱신.
     * 레이스로 잠깐 OFFLINE 이 됐어도(연결은 살아있으니) ONLINE 으로 자가치유.
     * 단 RUNNING 중이면 건드리지 않는다.
     */
    public void touchHeartbeat(LocalDateTime now) {
        this.lastHeartbeatAt = now;
        if (this.status == WorkerStatus.OFFLINE) {
            this.status = WorkerStatus.ONLINE;
        }
    }

    /** 연결 종료 / 하트비트 타임아웃: 오프라인. */
    public void goOffline() {
        this.status = WorkerStatus.OFFLINE;
    }

    /**
     * JOB_ACCEPTED: 작업 시작 → RUNNING + 현재 작업 기록.
     * (스위퍼가 찰나에 OFFLINE 으로 내렸어도 RUNNING 으로 덮는 것은 의도 —
     *  JOB_ACCEPTED 수신 자체가 연결이 살아있다는 증거)
     */
    public void startJob(Long jobId) {
        this.status = WorkerStatus.RUNNING;
        this.currentJobId = jobId;
    }

    /** JOB_RESULT/FAILED/CANCELLED: 작업 종료 → 다시 대기(ONLINE) + 현재 작업 비움. */
    public void finishJob() {
        this.status = WorkerStatus.ONLINE;
        this.currentJobId = null;
    }

    /**
     * 작업 후보 자격 — 규격 §28 "operator 조건 충족".
     * 에스크로에 넣을 정산 주소(provider)와 트랜잭션에 서명할 주소(operator)가 모두 있어야
     * 배정해도 acceptJob() 까지 갈 수 있다. 둘 중 하나라도 없으면 후보에서 뺀다.
     */
    public boolean isEscrowReady() {
        return operatorAddress != null && owner.getWalletAddress() != null;
    }
}
