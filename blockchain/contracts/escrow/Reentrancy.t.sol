// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {GPUJobEscrow} from "./GPUJobEscrow.sol";
import {ReentrantToken} from "../mocks/ReentrantToken.sol";
import {ReentrantClient, ReentrantProvider} from "../mocks/ReentrancyAttacker.sol";

/// @title 재진입 공격 재현
/// @notice CEI 순서를 지켜서 막히는 것을 실제 공격으로 확인한다. 주장만 남기지 않는다.
/// @dev 에스크로에 피해자 작업의 돈을 함께 넣어 둔다. 재진입이 성공하면
///      공격자가 남의 예치금까지 가져갈 수 있어야 공격이 성립하기 때문이다.
///
///      지급 경로가 submitResult 하나로 모이면서 재진입 표면이 그쪽으로 옮겨 갔다.
///      abortJob 도 자금을 옮기므로 함께 시험한다.
contract ReentrancyTest is Test {
    ReentrantToken    internal token;
    GPUJobEscrow      internal escrow;
    ReentrantClient   internal atkClient;
    ReentrantProvider internal atkProvider;

    address internal victim  = address(0x71C71);
    address internal honestC = address(0xC11E27);

    uint64  internal constant ACCEPT_TIMEOUT    = 2 hours;
    uint64  internal constant EXECUTION_TIMEOUT = 1 hours;
    uint256 internal constant PRICE             = 10 ether;
    uint256 internal constant SEED              = 1_000 ether;
    bytes32 internal constant RESULT_HASH       = keccak256("result");
    bytes32 internal constant EXTERNAL_REF      = keccak256("job");

    function setUp() public {
        token  = new ReentrantToken(1_000_000 ether);
        escrow = new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT);

        atkClient   = new ReentrantClient(address(escrow), address(token));
        atkProvider = new ReentrantProvider(address(escrow));

        token.transfer(address(atkClient), SEED);
        token.transfer(victim,  SEED);
        token.transfer(honestC, SEED);
    }

    /// @dev 피해자가 예치해 둔 돈. 재진입이 성공하면 이게 털린다.
    function _seedVictimJob() internal returns (uint256 jobId) {
        vm.prank(victim);
        token.approve(address(escrow), PRICE);
        vm.prank(victim);
        jobId = escrow.createAndFundJob(EXTERNAL_REF, PRICE, address(atkProvider));
    }

    /// @dev 정직한 의뢰자가 공격 공급자에게 맡긴 작업. ASSIGNED 까지 올려 둔다.
    function _assignedToAttacker() internal returns (uint256 jobId) {
        vm.prank(honestC);
        token.approve(address(escrow), PRICE);
        vm.prank(honestC);
        jobId = escrow.createAndFundJob(EXTERNAL_REF, PRICE, address(atkProvider));

        atkProvider.accept(jobId);
    }

    // ── 훅 토큰이 실제로 콜백을 부르는가 ─────────────────────────

    /// @dev 이 확인이 없으면 "공격이 시도조차 안 됐는데 통과" 를 구분할 수 없다.
    function test_hookTokenActuallyCallsBack() public {
        atkClient.createAndFund(PRICE, address(atkProvider));
        atkClient.attackCancel();

        assertGt(atkClient.callbackCount(), 0, "callback never fired");
        assertGt(atkClient.reenterCount(), 0, "no reentry attempted");
    }

    // ── 환불 경로 재진입 ─────────────────────────────────────────

    function test_cancelJob_reentrancyIsBlocked() public {
        _seedVictimJob();
        uint256 escrowBefore = token.balanceOf(address(escrow));

        atkClient.createAndFund(PRICE, address(atkProvider));
        uint256 attackerBefore = token.balanceOf(address(atkClient));

        atkClient.attackCancel();

        assertTrue(atkClient.reenterFailed(), "reentrancy was NOT blocked");
        // 자기 예치금만 돌려받았다. 피해자 돈은 그대로다.
        assertEq(token.balanceOf(address(atkClient)), attackerBefore + PRICE);
        assertEq(token.balanceOf(address(escrow)), escrowBefore);
    }

    function test_settleTimeout_reentrancyIsBlocked() public {
        _seedVictimJob();
        uint256 jobId = _assignedToAttacker();

        (, , uint64 dl, , , , ) = escrow.jobs(jobId);
        vm.warp(dl + 1);

        uint256 honestBefore = token.balanceOf(honestC);
        atkProvider.attackSettle();

        // 환불이므로 공격자에게는 아무것도 오지 않는다. 콜백도 의뢰자 쪽에서 돈다.
        assertEq(token.balanceOf(address(atkProvider)), 0);
        assertEq(token.balanceOf(honestC), honestBefore + PRICE);
        // 피해자 예치금은 그대로다.
        assertEq(token.balanceOf(address(escrow)), PRICE);
    }

    // ── 지급 경로 재진입 ─────────────────────────────────────────
    //
    // submitResult 가 결과 봉인과 지급을 함께 하므로 재진입 표면이 여기로 옮겨 왔다.

    /// @notice 대금을 받는 콜백에서 submitResult 를 다시 부른다.
    function test_submitResult_reentrancyIsBlocked() public {
        _seedVictimJob();
        _assignedToAttacker();

        uint256 attackerBefore = token.balanceOf(address(atkProvider));

        atkProvider.attackSubmit(RESULT_HASH, 1); // 콜백에서 submitResult 재호출

        assertGt(atkProvider.callbackCount(), 0, "callback never fired");
        assertTrue(atkProvider.reenterFailed(), "reentrancy was NOT blocked");
        // 대금을 한 번만 받았다.
        assertEq(token.balanceOf(address(atkProvider)), attackerBefore + PRICE);
        // 피해자 예치금은 그대로다.
        assertEq(token.balanceOf(address(escrow)), PRICE);
    }

    /// @notice 대금을 받는 콜백에서 abortJob 을 불러 환불까지 받아 내려 한다.
    function test_submitResult_crossFunctionAbortIsBlocked() public {
        _seedVictimJob();
        _assignedToAttacker();

        uint256 attackerBefore = token.balanceOf(address(atkProvider));
        uint256 honestBefore   = token.balanceOf(honestC);

        atkProvider.attackSubmit(RESULT_HASH, 2); // 콜백에서 abortJob 재호출

        assertTrue(atkProvider.reenterFailed(), "cross-function reentrancy was NOT blocked");
        assertEq(token.balanceOf(address(atkProvider)), attackerBefore + PRICE);
        // 지급이 났으므로 의뢰자에게 환불이 가면 안 된다.
        assertEq(token.balanceOf(honestC), honestBefore);
        assertEq(token.balanceOf(address(escrow)), PRICE);
    }

    /// @notice 반납으로 환불이 나가는 동안 재진입을 시도한다.
    function test_abortJob_reentrancyIsBlocked() public {
        _seedVictimJob();
        _assignedToAttacker();

        uint256 honestBefore = token.balanceOf(honestC);
        atkProvider.attackAbort();

        // 환불은 의뢰자에게 한 번만 간다.
        assertEq(token.balanceOf(honestC), honestBefore + PRICE);
        assertEq(token.balanceOf(address(atkProvider)), 0);
        assertEq(token.balanceOf(address(escrow)), PRICE);
    }

    // ── 교차 함수 재진입 ─────────────────────────────────────────

    /// @dev createAndFundJob 이 transferFrom 을 도는 동안 cancelJob 을 부른다.
    ///      그 시점에 상태는 이미 FUNDED 이고 호출자도 job.client 본인이라
    ///      CEI 검사 둘을 모두 통과한다. nonReentrant 가 없으면 성공한다.
    function test_createAndFundJob_crossFunctionReentrancyIsBlocked() public {
        uint256 before = token.balanceOf(address(atkClient));

        uint256 jobId = atkClient.attackFundThenCancel(PRICE, address(atkProvider));

        assertGt(atkClient.crossCallbackCount(), 0, "sender callback never fired");
        assertGt(atkClient.crossReenterCount(), 0, "no cross-function reentry attempted");
        assertTrue(atkClient.crossReenterFailed(), "cross-function reentrancy was NOT blocked");

        // 예치는 정상적으로 끝나고 취소되지 않았다
        (, GPUJobEscrow.JobState st, , , , , ) = escrow.jobs(jobId);
        assertEq(uint8(st), uint8(GPUJobEscrow.JobState.FUNDED));
        assertEq(token.balanceOf(address(escrow)), PRICE);
        assertEq(token.balanceOf(address(atkClient)), before - PRICE);
    }

    // ── 정상 흐름은 훅 토큰에서도 그대로 ─────────────────────────

    function test_normalFlowStillWorksWithHookToken() public {
        vm.prank(honestC);
        token.approve(address(escrow), PRICE);
        vm.prank(honestC);
        uint256 jobId = escrow.createAndFundJob(EXTERNAL_REF, PRICE, address(atkProvider));

        atkProvider.accept(jobId);

        uint256 before = token.balanceOf(address(atkProvider));
        atkProvider.attackSubmit(RESULT_HASH, 0); // armed 이지만 재진입은 막힌다

        assertEq(token.balanceOf(address(atkProvider)), before + PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }
}
