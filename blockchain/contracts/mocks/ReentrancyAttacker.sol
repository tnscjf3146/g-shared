// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

interface IEscrowTarget {
    function createJob(uint256 price) external returns (uint256);
    function fundJob(uint256 jobId) external;
    function cancelJob(uint256 jobId) external;
    function acceptJob(uint256 jobId) external;
    function submitResult(uint256 jobId, bytes32 resultHash) external;
    function settleTimeout(uint256 jobId) external;
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

    function createAndFund(uint256 price) external returns (uint256) {
        jobId = escrow.createJob(price);
        token.approve(address(escrow), price);
        escrow.fundJob(jobId);
        return jobId;
    }

    /// @notice 여기서부터 공격이다.
    function attackCancel() external {
        armed = true;
        escrow.cancelJob(jobId);
        armed = false;
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
contract ReentrantProvider {
    IEscrowTarget public immutable escrow;

    uint256 public jobId;
    bool    public armed;
    uint256 public callbackCount;
    uint256 public reenterCount;
    bool    public reenterFailed;

    constructor(address escrow_) {
        escrow = IEscrowTarget(escrow_);
    }

    function accept(uint256 jobId_) external {
        jobId = jobId_;
        escrow.acceptJob(jobId_);
    }

    function submit(bytes32 resultHash) external {
        escrow.submitResult(jobId, resultHash);
    }

    /// @notice 만료 정산으로 대금을 받으면서 재진입을 시도한다.
    function attackSettle() external {
        armed = true;
        escrow.settleTimeout(jobId);
        armed = false;
    }

    /// @notice 의뢰자 승인으로 대금을 받는 경우에도 콜백은 온다.
    function arm() external {
        armed = true;
    }

    function onTokenReceived(address, uint256) external {
        if (!armed) return;
        callbackCount++;

        reenterCount++;
        try escrow.settleTimeout(jobId) {
        } catch {
            reenterFailed = true;
        }
    }
}
