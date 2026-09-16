package com.gshared.back.worker.domain;

/**
 * "이 워커의 operator 키를 공급자가 온체인에서 승인했는가"에 대한 답.
 *
 * <p>답이 다섯인 것은 "아니오"에 종류가 있어서다. 승인을 안 한 것과, 승인할 지갑이 없는 것과,
 * 물어볼 수 없었던 것은 화면에서 안내가 전부 다르다 — 지갑을 연결하라 / MetaMask 로 승인하라 /
 * 잠시 후 다시 보라. 하나로 접으면 사용자는 무엇을 해야 하는지 모른다.
 *
 * <p>저장하지 않는다. operator 키는 재등록·초기화로 바뀔 수 있고 승인은 체인이 갖고 있으므로,
 * 물을 때마다 지금 {@code workers.operator_address} 로 체인에 다시 묻는다.
 */
public enum OperatorApproval {
    /** 체인이 {@code isOperator(provider, operator) == true} 라고 답했다. */
    APPROVED,
    /** 체인이 false 라고 답했다 — 공급자가 MetaMask 로 {@code setOperator(operator, true)} 를 보내야 한다. */
    NOT_APPROVED,
    /** 공급자 지갑({@code users.wallet_address})이 없다 — 승인할 주체가 없다. */
    PROVIDER_WALLET_MISSING,
    /** 워커에 operator 주소가 없다 — 승인할 대상이 없다. */
    OPERATOR_MISSING,
    /** 물어보지 못했다 — 체인이 설정되지 않았거나 노드가 답하지 않았다. 승인 여부를 모른다. */
    UNVERIFIED;

    public boolean isApproved() {
        return this == APPROVED;
    }
}
