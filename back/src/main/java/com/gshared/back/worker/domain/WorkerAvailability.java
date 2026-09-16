package com.gshared.back.worker.domain;

/**
 * 이 워커가 <b>지금 Job 을 받을 수 있는가</b>. {@link WorkerStatus} 와 다르다.
 *
 * <p>ONLINE 은 연결 상태일 뿐이다. 실제로 후보에 오르려면 맡은 작업이 없고, 정산 지갑이 있고,
 * 공급자가 operator 키를 온체인에서 승인했어야 한다({@code WorkerRepository.findAvailableWorkers}
 * + {@code JobService} 의 operator 필터). 그 조건을 하나라도 못 채우면 ONLINE 인데도 Job 이
 * 오지 않고, 공급자는 이유를 알 수 없었다. 이 값은 그 이유를 이름 붙인 것이다.
 *
 * <p>저장하지 않고 조회할 때 계산한다. 앞쪽 조건(연결·작업)이 걸리면 뒤쪽(체인)은 묻지 않는다.
 */
public enum WorkerAvailability {
    /** 연결·미배정·지갑·승인 모두 충족 — 후보에 오른다. */
    AVAILABLE,
    /** 작업 중. 끝나면 다시 AVAILABLE 로 돌아온다. */
    BUSY,
    /** 연결이 끊겼거나 하트비트가 멎었다. */
    OFFLINE,
    /** 공급자 지갑이 없다 — 예치할 때 넣을 정산 주소가 없어 후보에서 빠진다. */
    WALLET_REQUIRED,
    /** 공급자가 이 operator 키를 아직 승인하지 않았다(또는 operator 주소가 없다). */
    OPERATOR_APPROVAL_REQUIRED,
    /**
     * 체인이 설정돼 있는데 승인 여부를 확인하지 못했다(노드 무응답 등).
     * 매칭은 모를 때 거르지 않으므로 후보에 오를 수는 있지만, 확인된 상태는 아니다.
     */
    CHAIN_VERIFICATION_FAILED;

    /**
     * 승인 상태와 연결·작업 상태로 가용성을 정한다.
     *
     * @param chainConfigured 체인에 물어볼 수 있는 설정인가. 아니면 UNVERIFIED 는 "확인할 것이 없다"이지
     *                        "확인에 실패했다"가 아니다 — 로컬에서 체인 없이 돌릴 때 모든 워커가
     *                        CHAIN_VERIFICATION_FAILED 로 보이면 안 된다.
     */
    public static WorkerAvailability of(WorkerStatus status, Long currentJobId,
                                        OperatorApproval approval, boolean chainConfigured) {
        if (status == WorkerStatus.OFFLINE) {
            return OFFLINE;
        }
        if (status == WorkerStatus.RUNNING || currentJobId != null) {
            return BUSY;
        }
        return switch (approval) {
            case APPROVED -> AVAILABLE;
            case NOT_APPROVED, OPERATOR_MISSING -> OPERATOR_APPROVAL_REQUIRED;
            case PROVIDER_WALLET_MISSING -> WALLET_REQUIRED;
            case UNVERIFIED -> chainConfigured ? CHAIN_VERIFICATION_FAILED : AVAILABLE;
        };
    }
}
