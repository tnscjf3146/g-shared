package com.gshared.back.worker.dto.response;

/**
 * 네트워크 전체 워커 현황 (소비자 대시보드 KPI).
 * onlineWorkers 는 지금 작업을 받을 수 있거나 하고 있는 GPU(ONLINE·RUNNING), totalWorkers 는 등록된 전체.
 */
public record NetworkStatsResponse(
        long onlineWorkers,
        long totalWorkers
) {
}
