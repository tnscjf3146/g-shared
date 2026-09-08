package com.gshared.back.job.domain;

/**
 * Job(학습 주문) 상태 전이.
 * <pre>
 * CREATED ─(예치 확인: BC 연동)→ FUNDED ─(매칭)→ ASSIGNED ─(JOB_ACCEPTED)→ RUNNING
 *                                                            ├→ COMPLETED (JOB_RESULT)
 *                                                            ├→ FAILED    (JOB_FAILED)
 * CREATED/FUNDED/ASSIGNED ─(사용자 취소)→ CANCELLED           ┘
 * </pre>
 */
public enum JobStatus {
    CREATED,    // 주문서 생성됨, 예치(입금) 확인 대기
    FUNDED,     // 예치 확인 완료, Worker 배정 대기
    ASSIGNED,   // Worker 배정됨, 실행 시작 대기
    RUNNING,    // GPU 학습 진행 중
    COMPLETED,  // 정상 완료 (종료)
    FAILED,     // 실행 실패 (종료)
    CANCELLED   // 취소/기한만료 (종료)
}
