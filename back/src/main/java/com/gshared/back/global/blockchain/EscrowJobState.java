package com.gshared.back.global.blockchain;

/**
 * 온체인 {@code GPUJobEscrow.JobState}.
 *
 * <p><b>정본은 {@code blockchain/abi/GPUJobEscrow.json} 의 stateEnum 이다.</b> 그 파일은
 * 컨트랙트 소스에서 생성되고 sourceCommit 을 함께 들고 있다. 여기 값은 그것을 그대로
 * 옮긴 것이고, 순서(= 숫자)가 계약이다 -- 컨트랙트가 열거형에 항목을 끼워 넣으면
 * 같은 숫자가 다른 뜻이 된다.
 *
 * <p>Worker 는 같은 표를 갖고 있고 ABI 를 읽을 때 교차 검증한다
 * ({@code gshared_worker.escrow_abi.check_contract_matches}). Backend 는 ABI 파일이
 * 다른 모듈에 있어 실행 중에 읽지 않으므로, 컨트랙트가 바뀌면 세 곳을 함께 고쳐야 한다.
 */
public enum EscrowJobState {

    /** 그런 Job 이 없다. 읽지 않은 슬롯은 전부 0 이다. */
    NONE(0),
    /** 예치됨. 아직 아무도 가져가지 않았다. */
    FUNDED(1),
    /** 어느 operator 가 수락했다. */
    ASSIGNED(2),
    /** 결과가 기록되고 정산됐다. */
    COMPLETED(3),
    /** 취소·환불됐다. */
    CANCELLED(4);

    private final int code;

    EscrowJobState(int code) {
        this.code = code;
    }

    public int code() {
        return code;
    }

    /** 온체인 uint8 을 상태로. 모르는 값은 예외 -- 조용히 NONE 으로 읽지 않는다. */
    public static EscrowJobState of(int code) {
        for (EscrowJobState state : values()) {
            if (state.code == code) {
                return state;
            }
        }
        throw new IllegalArgumentException("알 수 없는 온체인 JobState: " + code);
    }
}
