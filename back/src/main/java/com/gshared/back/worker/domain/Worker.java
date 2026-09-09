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

    // 공급자(주인). 정산 시 이 회원에게 토큰 지급.
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "owner_id", nullable = false)
    private User owner;

    @Column(nullable = false, unique = true)   // DB: uk_workers_token (V3 마이그레이션)
    private String workerToken;        // WebSocket 인증 토큰의 SHA-256 해시 (원문은 발급 시 1회만 노출)

    // 하드웨어 스펙 (register 시점 스냅샷)
    private String deviceName;
    private String osVersion;
    private String gpuModel;
    private Integer vramMb;
    private String driverVersion;
    private Double benchmarkTokensPerSec;

    // 실시간 상태 (WebSocket 단계에서 갱신)
    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private WorkerStatus status;

    private String currentJobId;
    private LocalDateTime lastHeartbeatAt;

    private Worker(String id, User owner, String workerToken,
                   String deviceName, String osVersion, String gpuModel,
                   Integer vramMb, String driverVersion, Double benchmarkTokensPerSec) {
        this.id = id;
        this.owner = owner;
        this.workerToken = workerToken;
        this.deviceName = deviceName;
        this.osVersion = osVersion;
        this.gpuModel = gpuModel;
        this.vramMb = vramMb;
        this.driverVersion = driverVersion;
        this.benchmarkTokensPerSec = benchmarkTokensPerSec;
        this.status = WorkerStatus.OFFLINE;   // 등록 직후엔 아직 연결 전
    }

    public static Worker register(String id, User owner, String workerToken,
                                  String deviceName, String osVersion, String gpuModel,
                                  Integer vramMb, String driverVersion, Double benchmarkTokensPerSec) {
        return new Worker(id, owner, workerToken, deviceName, osVersion, gpuModel,
                vramMb, driverVersion, benchmarkTokensPerSec);
    }

    /**
     * GPU 정보 갱신. 그래픽카드 교체·드라이버 갱신·벤치마크 재측정 시 워커가 다시 알려준다.
     * 신원(id·토큰·주인)과 실시간 상태(status·currentJobId)는 건드리지 않는다 —
     * 여기서 바뀌는 것은 "이 워커가 무엇을 제공하는가" 뿐이다.
     */
    public void updateGpu(String gpuModel, Integer vramMb,
                          String driverVersion, Double benchmarkTokensPerSec) {
        this.gpuModel = gpuModel;
        this.vramMb = vramMb;
        this.driverVersion = driverVersion;
        this.benchmarkTokensPerSec = benchmarkTokensPerSec;
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
    public void startJob(String jobId) {
        this.status = WorkerStatus.RUNNING;
        this.currentJobId = jobId;
    }

    /** JOB_RESULT/FAILED: 작업 종료 → 다시 대기(ONLINE) + 현재 작업 비움. */
    public void finishJob() {
        this.status = WorkerStatus.ONLINE;
        this.currentJobId = null;
    }
}
