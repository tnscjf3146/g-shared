package com.gshared.back.worker.websocket.protocol;

/**
 * Worker ↔ Backend 사이에 오가는 메시지 종류.
 * (이형동 WebSocket 명세의 Message Type 전체)
 */
public enum WorkerMessageType {

    // ── Worker → Backend (수신) ─────────────────────────
    REGISTER,       // 연결 후 등록
    HEARTBEAT,      // 30초마다 살아있음 신호
    JOB_ACCEPTED,   // 작업 수신·실행 시작
    JOB_PROGRESS,   // 진행률
    JOB_RESULT,     // 작업 성공
    JOB_FAILED,     // 작업 실패/취소완료

    // ── Backend → Worker (발신) ─────────────────────────
    REGISTER_ACK,   // 등록 확인
    HEARTBEAT_ACK,  // 하트비트 응답
    JOB_ASSIGN,     // 작업 배정
    JOB_CANCEL      // 작업 취소 요청
}
