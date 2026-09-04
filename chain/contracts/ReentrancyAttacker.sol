// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IEscrow {
    function acceptJob(uint256 jobId) external;
    function submitResult(uint256 jobId, bytes32 resultHash) external;
}

/// 재진입 공격을 재현하는 악성 공급자.
///
/// 토큰을 받는 순간(onTokenReceived) 에스크로를 다시 호출한다.
/// 에스크로가 상태를 먼저 바꾸지 않았다면 여기서 대금이 두 번 나간다.
contract ReentrancyAttacker {
    IEscrow public immutable escrow;
    uint256 public jobId;
    uint256 public reenterCount;
    bool public reenterFailed;

    constructor(address escrow_) {
        escrow = IEscrow(escrow_);
    }

    function accept(uint256 jobId_) external {
        jobId = jobId_;
        escrow.acceptJob(jobId_);
    }

    function submit(bytes32 resultHash) external {
        escrow.submitResult(jobId, resultHash);
    }

    /// MockSSF 가 이 컨트랙트로 토큰을 보낼 때 호출한다.
    /// 여기서 정산 함수를 다시 부르는 것이 재진입 공격이다.
    function onTokenReceived(address, uint256) external {
        if (msg.sender == address(0)) return;
        reenterCount++;
        try escrow.submitResult(jobId, bytes32(uint256(1))) {
            // 상태가 이미 바뀌었으면 여기 도달하지 않는다
        } catch {
            reenterFailed = true;
        }
    }
}
