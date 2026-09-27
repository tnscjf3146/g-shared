package com.gshared.back.worker.domain;

/**
 * Worker(공급자 PC) 연결/작업 상태.
 * - OFFLINE : WebSocket 미연결 또는 Heartbeat 90초 초과
 * - ONLINE  : 연결됨, 실행 중 Job 없음 (작업 배정 가능)
 * - RUNNING : GPU 작업 실행 중 (JOB_ACCEPTED 이후)
 */
public enum WorkerStatus {
    OFFLINE,
    ONLINE,
    RUNNING
}
