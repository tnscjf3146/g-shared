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
contract ReentrancyTest is Test {
    ReentrantToken    internal token;
    GPUJobEscrow      internal escrow;
    ReentrantClient   internal atkClient;
    ReentrantProvider internal atkProvider;

    address internal victim   = address(0x71C71);
    address internal honestC  = address(0xC11E27);

    uint64  internal constant ACCEPT_DEADLINE  = 2 hours;
    uint64  internal constant EXEC_DEADLINE    = 1 hours;
    uint64  internal constant APPROVE_DEADLINE = 30 minutes;
    uint256 internal constant PRICE            = 10 ether;
    uint256 internal constant SEED             = 1_000 ether;
    bytes32 internal constant RESULT_HASH      = keccak256("result");

    function setUp() public {
        token  = new ReentrantToken(1_000_000 ether);
        escrow = new GPUJobEscrow(address(token), ACCEPT_DEADLINE, EXEC_DEADLINE, APPROVE_DEADLINE);

        atkClient   = new ReentrantClient(address(escrow), address(token));
        atkProvider = new ReentrantProvider(address(escrow));

        token.transfer(address(atkClient), SEED);
        token.transfer(victim,  SEED);
        token.transfer(honestC, SEED);
    }

    /// @dev 피해자가 예치해 둔 돈. 재진입이 성공하면 이게 털린다.
    function _seedVictimJob() internal returns (uint256 jobId) {
        vm.prank(victim);
        jobId = escrow.createJob(PRICE);
        vm.prank(victim);
        token.approve(address(escrow), PRICE);
        vm.prank(victim);
        escrow.fundJob(jobId);
    }

    // ── 훅 토큰이 실제로 콜백을 부르는가 ─────────────────────────

    /// @dev 이 확인이 없으면 "공격이 시도조차 안 됐는데 통과" 를 구분할 수 없다.
    function test_hookTokenActuallyCallsBack() public {
        atkClient.createAndFund(PRICE);
        atkClient.attackCancel();

        assertGt(atkClient.callbackCount(), 0, "callback never fired");
        assertGt(atkClient.reenterCount(), 0, "no reentry attempted");
    }

    // ── cancelJob 재진입 ─────────────────────────────────────────

    function test_cancelJob_reentrancyIsBlocked() public {
        _seedVictimJob();
        assertEq(token.balanceOf(address(escrow)), PRICE);

        atkClient.createAndFund(PRICE);
        assertEq(token.balanceOf(address(escrow)), PRICE * 2);

        atkClient.attackCancel();

        assertTrue(atkClient.reenterFailed(), "reentrancy was NOT blocked");
        assertEq(token.balanceOf(address(atkClient)), SEED, "attacker drained extra funds");
        assertEq(token.balanceOf(address(escrow)), PRICE, "victim deposit was stolen");
    }

    // ── settleTimeout 재진입 ─────────────────────────────────────

    function test_settleTimeout_reentrancyIsBlocked() public {
        _seedVictimJob();

        vm.prank(honestC);
        uint256 jobId = escrow.createJob(PRICE);
        vm.prank(honestC);
        token.approve(address(escrow), PRICE);
        vm.prank(honestC);
        escrow.fundJob(jobId);

        atkProvider.accept(jobId);
        atkProvider.submit(RESULT_HASH);

        (, , uint64 dl, , , ) = escrow.jobs(jobId);
        vm.warp(dl + 1);

        assertEq(token.balanceOf(address(escrow)), PRICE * 2);

        atkProvider.attackSettle();

        assertGt(atkProvider.callbackCount(), 0, "callback never fired");
        assertTrue(atkProvider.reenterFailed(), "reentrancy was NOT blocked");
        assertEq(token.balanceOf(address(atkProvider)), PRICE, "provider was paid twice");
        assertEq(token.balanceOf(address(escrow)), PRICE, "victim deposit was stolen");
    }

    // ── approveResult 재진입 ─────────────────────────────────────

    function test_approveResult_reentrancyIsBlocked() public {
        _seedVictimJob();

        vm.prank(honestC);
        uint256 jobId = escrow.createJob(PRICE);
        vm.prank(honestC);
        token.approve(address(escrow), PRICE);
        vm.prank(honestC);
        escrow.fundJob(jobId);

        atkProvider.accept(jobId);
        atkProvider.submit(RESULT_HASH);

        atkProvider.arm();

        vm.prank(honestC);
        escrow.approveResult(jobId);

        assertGt(atkProvider.callbackCount(), 0, "callback never fired");
        assertTrue(atkProvider.reenterFailed(), "reentrancy was NOT blocked");
        assertEq(token.balanceOf(address(atkProvider)), PRICE, "provider was paid twice");
        assertEq(token.balanceOf(address(escrow)), PRICE, "victim deposit was stolen");
    }

    // ── 정상 흐름은 훅 토큰에서도 그대로 동작한다 ────────────────

    function test_normalFlowStillWorksWithHookToken() public {
        vm.prank(honestC);
        uint256 jobId = escrow.createJob(PRICE);
        vm.prank(honestC);
        token.approve(address(escrow), PRICE);
        vm.prank(honestC);
        escrow.fundJob(jobId);

        atkProvider.accept(jobId);
        atkProvider.submit(RESULT_HASH);

        vm.prank(honestC);
        escrow.approveResult(jobId);

        assertEq(token.balanceOf(address(atkProvider)), PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }
}
