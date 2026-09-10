package com.gshared.back.worker.dto.response;

import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;

import java.time.LocalDateTime;

/**
 * 내 워커 한 대의 현재 모습. 공급자가 자기 PC 상태를 보는 화면에 그대로 나간다.
 *
 * <p>워커 토큰은 절대 싣지 않는다 — 등록 시 1회만 노출하는 값이고, 조회로 다시 꺼낼 수
 * 있으면 1회 노출이라는 전제가 무너진다.
 */
public record WorkerResponse(
        String workerId,
        String deviceName,
        String osVersion,
        String gpuModel,
        Integer vramMb,
        String driverVersion,
        Double benchmarkTokensPerSec,
        WorkerStatus status,
        String currentJobId,
        LocalDateTime lastHeartbeatAt,
        LocalDateTime createdAt,
        boolean walletLinked
) {
    public static WorkerResponse from(Worker worker) {
        return new WorkerResponse(
                worker.getId(),
                // 등록 때 필수로 받는 값이지만 옛 행에 빈 값이 있을 수 있어 화면이 깨지지 않게 막는다.
                worker.getDeviceName() == null ? "" : worker.getDeviceName(),
                worker.getOsVersion(),
                worker.getGpuModel() == null ? "" : worker.getGpuModel(),
                worker.getVramMb(),
                worker.getDriverVersion(),
                worker.getBenchmarkTokensPerSec(),
                worker.getStatus(),
                // 화면은 식별자를 문자열로 다룬다. 숫자로 내보내면 자릿수가 큰 id 에서 정밀도가 깎인다.
                worker.getCurrentJobId() == null ? null : String.valueOf(worker.getCurrentJobId()),
                worker.getLastHeartbeatAt(),
                worker.getCreatedAt(),
                // 지갑이 없으면 이 PC 는 작업 후보에 오르지 못한다(예치할 때 넣을 주소가 없다).
                // 그 사실을 아는 사람은 주인뿐이라 여기서 알려 준다.
                worker.getOwner().getWalletAddress() != null
        );
    }
}
