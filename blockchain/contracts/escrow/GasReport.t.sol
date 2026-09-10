// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test, console} from "forge-std/Test.sol";
import {GCoin} from "../GCoin.sol";
import {GPUJobEscrow} from "./GPUJobEscrow.sol";

/// @title 함수별 가스 사용량 측정
/// @notice 발표와 설계 근거용 수치를 남긴다. 검증 목적이 아니라 기록 목적이다.
contract GasReportTest is Test {
    GCoin        internal token;
    GPUJobEscrow internal escrow;

    address internal client   = address(0xC11E27);
    address internal provider = address(0x9401DE);

    uint64  internal constant ACCEPT_TIMEOUT    = 2 hours;
    uint64  internal constant EXECUTION_TIMEOUT = 1 hours;
    uint64  internal constant APPROVAL_TIMEOUT  = 30 minutes;
    uint256 internal constant PRICE             = 10 ether;
    bytes32 internal constant RESULT_HASH       = keccak256("result");

    function setUp() public {
        token  = new GCoin(client);
        escrow = new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT, APPROVAL_TIMEOUT);
    }

    function test_gasReport_normalPath() public {
        uint256 g;

        vm.startPrank(client);

        g = gasleft();
        uint256 jobId = escrow.createJob(PRICE);
        console.log("createJob      ", g - gasleft());

        g = gasleft();
        token.approve(address(escrow), PRICE);
        console.log("token.approve  ", g - gasleft());

        g = gasleft();
        escrow.fundJob(jobId, provider);
        console.log("fundJob        ", g - gasleft());

        vm.stopPrank();
        vm.startPrank(provider);

        g = gasleft();
        escrow.acceptJob(jobId);
        console.log("acceptJob      ", g - gasleft());

        g = gasleft();
        escrow.submitResult(jobId, RESULT_HASH);
        console.log("submitResult   ", g - gasleft());

        vm.stopPrank();
        vm.prank(client);

        g = gasleft();
        escrow.approveResult(jobId);
        console.log("approveResult  ", g - gasleft());
    }

    function test_gasReport_cancelAndTimeout() public {
        uint256 g;

        // cancelJob
        vm.startPrank(client);
        uint256 a = escrow.createJob(PRICE);
        token.approve(address(escrow), PRICE);
        escrow.fundJob(a, provider);
        g = gasleft();
        escrow.cancelJob(a);
        console.log("cancelJob      ", g - gasleft());
        vm.stopPrank();

        // settleTimeout · FUNDED 만료
        vm.startPrank(client);
        uint256 b = escrow.createJob(PRICE);
        token.approve(address(escrow), PRICE);
        escrow.fundJob(b, provider);
        vm.stopPrank();
        (, , uint64 dl, , , ) = escrow.jobs(b);
        vm.warp(dl + 1);
        g = gasleft();
        escrow.settleTimeout(b);
        console.log("settle FUNDED  ", g - gasleft());

        // settleTimeout · SUBMITTED 만료
        vm.startPrank(client);
        uint256 c = escrow.createJob(PRICE);
        token.approve(address(escrow), PRICE);
        escrow.fundJob(c, provider);
        vm.stopPrank();
        vm.startPrank(provider);
        escrow.acceptJob(c);
        escrow.submitResult(c, RESULT_HASH);
        vm.stopPrank();
        (, , uint64 dl2, , , ) = escrow.jobs(c);
        vm.warp(dl2 + 1);
        g = gasleft();
        escrow.settleTimeout(c);
        console.log("settle SUBMITTED", g - gasleft());
    }
}
