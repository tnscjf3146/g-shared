package com.gshared.back.worker.service;

import com.gshared.back.worker.domain.WorkerStatus;
import com.gshared.back.worker.dto.response.NetworkStatsResponse;
import com.gshared.back.worker.repository.WorkerRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.EnumSet;

/**
 * 네트워크 전체 워커 현황. 하트비트 만료는 {@code WorkerHeartbeatSweeper} 가 이미 status 를 OFFLINE 으로 바꿔 두므로
 * 조회 시점에 따로 판정하지 않고 status 만 센다 — 같은 판정이 두 곳에 살면 화면과 배정이 다른 답을 낸다.
 */
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class NetworkStatsService {

    private static final EnumSet<WorkerStatus> ACTIVE = EnumSet.of(WorkerStatus.ONLINE, WorkerStatus.RUNNING);

    private final WorkerRepository workerRepository;

    public NetworkStatsResponse getStats() {
        return new NetworkStatsResponse(workerRepository.countByStatusIn(ACTIVE), workerRepository.count());
    }
}
