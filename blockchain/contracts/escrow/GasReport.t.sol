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
    uint256 internal constant PRICE             = 10 ether;
    bytes32 internal constant RESULT_HASH       = keccak256("result");
    bytes32 internal constant EXTERNAL_REF      = keccak256("job");

    function setUp() public {
        token  = new GCoin(client);
        escrow = new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT);
    }

    /// @notice 정상 한 바퀴. 의뢰자는 승인·예치 두 번, 공급자는 수락·제출 두 번이다.
    function test_gasReport_normalPath() public {
        uint256 g;

        vm.startPrank(client);

        g = gasleft();
        token.approve(address(escrow), PRICE);
        console.log("token.approve    ", g - gasleft());

        g = gasleft();
        uint256 jobId = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        console.log("createAndFundJob ", g - gasleft());

        vm.stopPrank();
        vm.startPrank(provider);

        g = gasleft();
        escrow.acceptJob(jobId);
        console.log("acceptJob        ", g - gasleft());

        g = gasleft();
        escrow.submitResult(jobId, RESULT_HASH);
        console.log("submitResult     ", g - gasleft());

        vm.stopPrank();
    }

    /// @notice 환불 경로 셋. 지급 경로가 하나로 모여 여기는 전부 의뢰자에게 돌아간다.
    function test_gasReport_refundPaths() public {
        uint256 g;

        // cancelJob — 의뢰자가 직접 취소
        vm.startPrank(client);
        token.approve(address(escrow), PRICE);
        uint256 a = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        g = gasleft();
        escrow.cancelJob(a);
        console.log("cancelJob        ", g - gasleft());
        vm.stopPrank();

        // abortJob — 공급자가 반납
        vm.startPrank(client);
        token.approve(address(escrow), PRICE);
        uint256 b = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        vm.stopPrank();
        vm.startPrank(provider);
        escrow.acceptJob(b);
        g = gasleft();
        escrow.abortJob(b);
        console.log("abortJob         ", g - gasleft());
        vm.stopPrank();

        // settleTimeout · FUNDED 만료
        vm.startPrank(client);
        token.approve(address(escrow), PRICE);
        uint256 c = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        vm.stopPrank();
        (, , uint64 dl, , , , ) = escrow.jobs(c);
        vm.warp(dl + 1);
        g = gasleft();
        escrow.settleTimeout(c);
        console.log("settle FUNDED    ", g - gasleft());

        // settleTimeout · ASSIGNED 만료
        vm.startPrank(client);
        token.approve(address(escrow), PRICE);
        uint256 d = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        vm.stopPrank();
        vm.prank(provider);
        escrow.acceptJob(d);
        (, , uint64 dl2, , , , ) = escrow.jobs(d);
        vm.warp(dl2 + 1);
        g = gasleft();
        escrow.settleTimeout(d);
        console.log("settle ASSIGNED  ", g - gasleft());
    }
}
