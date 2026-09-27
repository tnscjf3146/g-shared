package com.gshared.back.worker.websocket.protocol;

/**
 * Worker ↔ Backend 사이에 오가는 메시지 종류.
 * (System ↔ Backend 연동 규격 v1 §7 의 Message Type 전체)
 */
public enum WorkerMessageType {

    // ── Worker → Backend (수신) ─────────────────────────
    REGISTER,       // 연결 후 등록 (identity 는 payload 가 아니라 핸드셰이크 토큰)
    HEARTBEAT,      // 30초마다 살아있음 신호
    JOB_ACCEPTED,   // acceptJob() 성공 — 실행 시작 (§13)
    JOB_REJECTED,   // acceptJob 전 검증 실패 — 배정 거절 (§14)
    JOB_PROGRESS,   // 진행률 (§16)
    JOB_RESULT,     // 업로드·submitResult() 까지 성공 (§19)
    JOB_FAILED,     // 실행 실패 (§20)
    JOB_CANCELLED,  // JOB_CANCEL 처리 완료 (§21)

    // ── Backend → Worker (발신) ─────────────────────────
    REGISTER_ACK,   // 등록 확인
    HEARTBEAT_ACK,  // 하트비트 응답
    JOB_ASSIGN,     // 작업 배정 알림 — 실행 데이터는 execution-context 로 조회 (§10)
    JOB_CANCEL      // 작업 취소 요청 (§21)
}
