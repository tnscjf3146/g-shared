// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

interface IEscrowTarget {
    function createAndFundJob(bytes32 externalRef, uint256 price, address provider)
        external
        returns (uint256);
    function cancelJob(uint256 jobId) external;
    function acceptJob(uint256 jobId) external;
    function submitResult(uint256 jobId, bytes32 resultHash) external;
    function abortJob(uint256 jobId) external;
    function settleTimeout(uint256 jobId) external;
    function nextJobId() external view returns (uint256);
}

interface IApprovable {
    function approve(address spender, uint256 amount) external returns (bool);
}

/// @title 재진입을 시도하는 악성 의뢰자
/// @notice 환불을 받는 순간 콜백에서 같은 함수를 다시 부른다.
/// @dev CEI 를 지키면 상태가 이미 CANCELLED 라 두 번째 호출이 막힌다.
///      상태 변경이 송금보다 뒤에 있었다면 여기서 에스크로 잔액이 두 번 빠져나간다.
contract ReentrantClient {
    IEscrowTarget public immutable escrow;
    IApprovable   public immutable token;

    uint256 public jobId;
    bool    public armed;         // 공격을 시작한 뒤에만 재진입한다
    uint256 public callbackCount; // 콜백이 실제로 불렸는가
    uint256 public reenterCount;  // 재진입을 몇 번 시도했는가
    bool    public reenterFailed; // 재진입이 막혔는가

    constructor(address escrow_, address token_) {
        escrow = IEscrowTarget(escrow_);
        token  = IApprovable(token_);
    }

    /// @param provider 예치할 때 지정할 공급자. 취소 경로 공격에서는 수락되지 않으므로 값만 채운다
    function createAndFund(uint256 price, address provider) external returns (uint256) {
        token.approve(address(escrow), price);
        jobId = escrow.createAndFundJob(bytes32(uint256(1)), price, provider);
        return jobId;
    }

    /// @notice 여기서부터 공격이다.
    function attackCancel() external {
        armed = true;
        escrow.cancelJob(jobId);
        armed = false;
    }

    // ── 교차 함수 재진입 ──────────────────────────────────────
    // createAndFundJob 이 transferFrom 을 도는 동안 cancelJob 을 부른다.
    // CEI 만으로는 막히지 않는다. 상태를 이미 FUNDED 로 바꿔 놓았고
    // 호출자도 job.client 본인이라 두 검사를 모두 통과하기 때문이다.
    // nonReentrant 가 여기서 일한다.

    bool    public crossArmed;
    uint256 public crossCallbackCount;
    uint256 public crossReenterCount;
    bool    public crossReenterFailed;

    function attackFundThenCancel(uint256 price, address provider) external returns (uint256) {
        token.approve(address(escrow), price);

        crossArmed = true;
        jobId = escrow.createAndFundJob(bytes32(uint256(2)), price, provider);
        crossArmed = false;

        return jobId;
    }

    /// @notice 토큰을 보내는 순간 호출된다. 교차 함수 재진입 통로다.
    /// @dev 예치가 도는 중이라 반환값을 아직 받지 못했다. 그런데 컨트랙트 안에서는
    ///      nextJobId 가 이미 증가했으므로 직전 값이 지금 만들어지는 작업 번호다.
    ///      이 값을 써야 실제로 그 작업을 겨냥한 재진입이 된다. 엉뚱한 번호로 부르면
    ///      상태 검사에 걸려 실패하고, 막혔는지 빗나갔는지 구분할 수 없다.
    function onTokenSent(address, uint256) external {
        if (!crossArmed) return;
        crossCallbackCount++;

        uint256 current = escrow.nextJobId() - 1;

        crossReenterCount++;
        try escrow.cancelJob(current) {
            // nonReentrant 가 없다면 여기 도달한다. 예치와 동시에 취소된다.
        } catch {
            crossReenterFailed = true;
        }
    }

    /// @notice 토큰을 받는 순간 호출된다. 재진입 통로다.
    function onTokenReceived(address, uint256) external {
        if (!armed) return;
        callbackCount++;

        reenterCount++;
        try escrow.cancelJob(jobId) {
            // CEI 를 어겼다면 여기 도달한다. 즉 두 번째 환불이 성공한다.
        } catch {
            reenterFailed = true;
        }
    }
}

/// @title 재진입을 시도하는 악성 공급자
/// @notice 대금을 받는 순간 콜백에서 정산 함수를 다시 부른다.
/// @dev 지급 경로가 submitResult 하나로 모여, 이 컨트랙트가 노리는 자리도 그곳이다.
contract ReentrantProvider {
    IEscrowTarget public immutable escrow;

    uint256 public jobId;
    bool    public armed;
    uint256 public callbackCount;
    uint256 public reenterCount;
    bool    public reenterFailed;

    /// @dev 콜백에서 어느 함수를 다시 부를지 고른다.
    ///      0 = settleTimeout, 1 = submitResult, 2 = abortJob
    uint8 public target;

    constructor(address escrow_) {
        escrow = IEscrowTarget(escrow_);
    }

    function accept(uint256 jobId_) external {
        jobId = jobId_;
        escrow.acceptJob(jobId_);
    }

    /// @notice 결과를 제출하며 재진입을 시도한다. 대금이 나가는 순간 콜백이 온다.
    function attackSubmit(bytes32 resultHash, uint8 target_) external {
        target = target_;
        armed = true;
        escrow.submitResult(jobId, resultHash);
        armed = false;
    }

    /// @notice 반납으로 환불이 나가는 순간에도 콜백은 온다. 다만 수령자는 의뢰자다.
    function attackAbort() external {
        armed = true;
        escrow.abortJob(jobId);
        armed = false;
    }

    /// @notice 만료 정산이 도는 동안 재진입을 시도한다.
    function attackSettle() external {
        armed = true;
        escrow.settleTimeout(jobId);
        armed = false;
    }

    function arm() external {
        armed = true;
    }

    function onTokenReceived(address, uint256) external {
        if (!armed) return;
        callbackCount++;
        reenterCount++;

        if (target == 1) {
            try escrow.submitResult(jobId, bytes32(uint256(0xdead))) {
            } catch {
                reenterFailed = true;
            }
        } else if (target == 2) {
            try escrow.abortJob(jobId) {
            } catch {
                reenterFailed = true;
            }
        } else {
            try escrow.settleTimeout(jobId) {
            } catch {
                reenterFailed = true;
            }
        }
    }

    /// @notice 토큰을 보내는 쪽 콜백. 이 컨트랙트는 보내는 일이 없어 쓰이지 않는다.
    function onTokenSent(address, uint256) external {}
}
