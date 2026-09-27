package com.gshared.back.job.domain;

/**
 * Job(학습 주문) 상태 전이.
 * <pre>
 * CREATED ─(예치 확인: BC 연동)→ FUNDED ─(매칭)→ ASSIGNED ─(JOB_ACCEPTED)→ RUNNING
 *                                              │  └(JOB_REJECTED)→ FAILED    ├→ COMPLETED (JOB_RESULT)
 *                                              └(RPC 장애 거절)→ FUNDED       ├→ FAILED    (JOB_FAILED)
 * CREATED/FUNDED ─(사용자 취소)→ CANCELLED                                     ┘
 * ASSIGNED/RUNNING ─(사용자 취소)→ CANCELLING ─(JOB_CANCELLED)→ CANCELLED
 *   (워커 응답이 없으면 CANCELLING 에 남는다 — 온체인 settleTimeout/reconciliation 으로 정리)
 * </pre>
 */
public enum JobStatus {
    CREATED,    // 주문서 생성됨, 예치(입금) 확인 대기
    FUNDED,     // 예치 확인 완료, Worker 배정 대기
    ASSIGNED,   // Worker 배정됨, 실행 시작 대기
    RUNNING,    // GPU 학습 진행 중
    COMPLETED,  // 정상 완료 (종료)
    FAILED,     // 실행 실패 (종료)
    CANCELLING, // 사용자 취소 요청됨 — 워커가 abortJob() 후 JOB_CANCELLED 로 답해야 CANCELLED (ASSIGNED/RUNNING 에서만)
    CANCELLED   // 취소/기한만료 (종료)
}
