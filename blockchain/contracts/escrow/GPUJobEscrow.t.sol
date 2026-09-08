// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {GCoin} from "../GCoin.sol";
import {GPUJobEscrow} from "./GPUJobEscrow.sol";

/// @title GPUJobEscrow 단위 테스트
/// @notice 조건 하나를 검증하는 테스트만 둔다. 전체 사용자 흐름은 GPUProtocol.e2e.ts 가 담당한다.
/// @dev 결제 토큰은 GCoin 을 그대로 쓴다. decimals 가 18 이므로 금액은 ether 단위로 적는다.
contract GPUJobEscrowTest is Test {
    GCoin        internal token;
    GPUJobEscrow internal escrow;

    address internal client   = address(0xC11E27);
    address internal provider = address(0x9401DE);
    address internal stranger = address(0x57A9E7);

    uint256 internal constant SSAFY_CHAIN_ID = 31221;
    uint64  internal constant ACCEPT_TIMEOUT  = 2 hours;
    uint64  internal constant EXECUTION_TIMEOUT    = 1 hours;
    uint64  internal constant APPROVAL_TIMEOUT = 30 minutes;
    uint256 internal constant PRICE          = 10 ether; // 10 GSC
    bytes32 internal constant RESULT_HASH    = keccak256("result");

    event JobCreated(uint256 indexed jobId, address indexed client, uint256 price);
    event JobFunded(uint256 indexed jobId, uint256 price, uint64 deadline);
    event JobAccepted(uint256 indexed jobId, address indexed provider, uint64 deadline);
    event ResultSubmitted(uint256 indexed jobId, address indexed provider, bytes32 resultHash, uint64 deadline);
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);
    event JobCancelled(uint256 indexed jobId, address indexed client, uint256 refund);
    event JobTimedOut(uint256 indexed jobId, GPUJobEscrow.JobState fromState);

    function setUp() public {
        token  = new GCoin(client);
        escrow = new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT, APPROVAL_TIMEOUT);
    }

    // ── 보조 함수 ────────────────────────────────────────────────

    function _created() internal returns (uint256 jobId) {
        vm.prank(client);
        jobId = escrow.createJob(PRICE);
    }

    function _funded() internal returns (uint256 jobId) {
        jobId = _created();
        vm.prank(client);
        token.approve(address(escrow), PRICE);
        vm.prank(client);
        escrow.fundJob(jobId);
    }

    function _assigned() internal returns (uint256 jobId) {
        jobId = _funded();
        vm.prank(provider);
        escrow.acceptJob(jobId);
    }

    function _submitted() internal returns (uint256 jobId) {
        jobId = _assigned();
        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);
    }

    function _state(uint256 jobId) internal view returns (GPUJobEscrow.JobState) {
        (, GPUJobEscrow.JobState s, , , , ) = escrow.jobs(jobId);
        return s;
    }

    function _deadline(uint256 jobId) internal view returns (uint64) {
        (, , uint64 dl, , , ) = escrow.jobs(jobId);
        return dl;
    }

    // ── 생성자 ───────────────────────────────────────────────────

    function test_constructor_rejectsZeroToken() public {
        vm.expectRevert(bytes("Escrow: token is zero"));
        new GPUJobEscrow(address(0), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT, APPROVAL_TIMEOUT);
    }

    function test_constructor_storesPaymentToken() public view {
        assertEq(address(escrow.paymentToken()), address(token));
        assertEq(escrow.acceptTimeout(), ACCEPT_TIMEOUT);
        assertEq(escrow.executionTimeout(), EXECUTION_TIMEOUT);
        assertEq(escrow.approvalTimeout(), APPROVAL_TIMEOUT);
    }

    function test_constructor_rejectsZeroDeadline() public {
        vm.expectRevert(bytes("Escrow: deadline is zero"));
        new GPUJobEscrow(address(token), 0, EXECUTION_TIMEOUT, APPROVAL_TIMEOUT);

        vm.expectRevert(bytes("Escrow: deadline is zero"));
        new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, 0, APPROVAL_TIMEOUT);

        vm.expectRevert(bytes("Escrow: deadline is zero"));
        new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT, 0);
    }

    /// @dev 취소·환불·타임아웃 구현이 끝나면 이 테스트와 컨트랙트의 가드를 함께 지운다.
    function test_constructor_revertsOnSsafyChain() public {
        vm.chainId(SSAFY_CHAIN_ID);

        vm.expectRevert(GPUJobEscrow.SsafyDeploymentBlocked.selector);
        new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT, APPROVAL_TIMEOUT);
    }

    function test_constructor_allowsOtherChains() public {
        vm.chainId(31337);

        GPUJobEscrow local = new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT, APPROVAL_TIMEOUT);
        assertEq(address(local.paymentToken()), address(token));
    }

    // ── createJob ────────────────────────────────────────────────

    function test_createJob_movesToCreated() public {
        uint256 jobId = _created();

        (address c, GPUJobEscrow.JobState s, uint64 dl, address p, uint256 price, bytes32 rh) = escrow.jobs(jobId);
        assertEq(jobId, 1);
        assertEq(c, client);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.CREATED));
        assertEq(dl, 0);
        assertEq(p, address(0));
        assertEq(price, PRICE);
        assertEq(rh, bytes32(0));
        assertEq(escrow.nextJobId(), 2);
    }

    function test_createJob_rejectsZeroPrice() public {
        vm.prank(client);
        vm.expectRevert(bytes("Escrow: price is zero"));
        escrow.createJob(0);
    }

    function test_createJob_emitsJobCreated() public {
        vm.expectEmit(true, true, false, true);
        emit JobCreated(1, client, PRICE);

        vm.prank(client);
        escrow.createJob(PRICE);
    }

    // ── fundJob ──────────────────────────────────────────────────

    function test_fundJob_failsWithoutApprove() public {
        uint256 jobId = _created();

        vm.prank(client);
        vm.expectRevert();
        escrow.fundJob(jobId);
    }

    function test_fundJob_movesTokensIntoEscrow() public {
        uint256 before = token.balanceOf(client);
        uint256 jobId  = _funded();

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.FUNDED));
        assertEq(token.balanceOf(client), before - PRICE);
        assertEq(token.balanceOf(address(escrow)), PRICE);
    }

    function test_fundJob_rejectsNonClient() public {
        uint256 jobId = _created();

        vm.prank(stranger);
        vm.expectRevert(bytes("Escrow: not the client"));
        escrow.fundJob(jobId);
    }

    function test_fundJob_rejectsWhenNotCreated() public {
        uint256 jobId = _funded();

        vm.prank(client);
        token.approve(address(escrow), PRICE);
        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not CREATED"));
        escrow.fundJob(jobId);
    }

    function test_fundJob_emitsJobFunded() public {
        uint256 jobId = _created();
        vm.prank(client);
        token.approve(address(escrow), PRICE);

        vm.expectEmit(true, false, false, true);
        emit JobFunded(jobId, PRICE, uint64(block.timestamp) + ACCEPT_TIMEOUT);

        vm.prank(client);
        escrow.fundJob(jobId);
    }

    // ── acceptJob ────────────────────────────────────────────────

    function test_acceptJob_movesToAssignedAndRecordsProvider() public {
        uint256 jobId = _funded();

        vm.prank(provider);
        escrow.acceptJob(jobId);

        (, GPUJobEscrow.JobState s, uint64 dl, address p, , ) = escrow.jobs(jobId);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.ASSIGNED));
        assertEq(p, provider);
        assertEq(dl, uint64(block.timestamp) + EXECUTION_TIMEOUT);
    }

    function test_acceptJob_rejectsBeforeFunding() public {
        uint256 jobId = _created();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.acceptJob(jobId);
    }

    function test_acceptJob_rejectsSecondAccept() public {
        uint256 jobId = _assigned();

        vm.prank(stranger);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.acceptJob(jobId);
    }

    function test_acceptJob_rejectsClient() public {
        uint256 jobId = _funded();

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: client cannot accept"));
        escrow.acceptJob(jobId);
    }

    function test_acceptJob_emitsJobAccepted() public {
        uint256 jobId = _funded();

        vm.expectEmit(true, true, false, true);
        emit JobAccepted(jobId, provider, uint64(block.timestamp) + EXECUTION_TIMEOUT);

        vm.prank(provider);
        escrow.acceptJob(jobId);
    }

    // ── submitResult ─────────────────────────────────────────────

    function test_submitResult_storesHashAndMovesToSubmitted() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        (, GPUJobEscrow.JobState s, uint64 dl, , , bytes32 rh) = escrow.jobs(jobId);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.SUBMITTED));
        assertEq(rh, RESULT_HASH);
        assertEq(dl, uint64(block.timestamp) + APPROVAL_TIMEOUT);
    }

    function test_submitResult_rejectsNonProvider() public {
        uint256 jobId = _assigned();

        vm.prank(stranger);
        vm.expectRevert(bytes("Escrow: not the provider"));
        escrow.submitResult(jobId, RESULT_HASH);
    }

    function test_submitResult_rejectsEmptyHash() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: empty result hash"));
        escrow.submitResult(jobId, bytes32(0));
    }

    function test_submitResult_rejectsWhenNotAssigned() public {
        uint256 jobId = _submitted();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.submitResult(jobId, RESULT_HASH);
    }

    function test_submitResult_emitsResultSubmitted() public {
        uint256 jobId = _assigned();

        vm.expectEmit(true, true, false, true);
        emit ResultSubmitted(jobId, provider, RESULT_HASH, uint64(block.timestamp) + APPROVAL_TIMEOUT);

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);
    }

    // ── approveResult ────────────────────────────────────────────

    function test_approveResult_paysProviderAndCompletes() public {
        uint256 jobId = _submitted();

        vm.prank(client);
        escrow.approveResult(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.COMPLETED));
        assertEq(token.balanceOf(provider), PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_approveResult_rejectsNonClient() public {
        uint256 jobId = _submitted();

        vm.prank(stranger);
        vm.expectRevert(bytes("Escrow: not the client"));
        escrow.approveResult(jobId);
    }

    function test_approveResult_rejectsBeforeSubmit() public {
        uint256 jobId = _assigned();

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not SUBMITTED"));
        escrow.approveResult(jobId);
    }

    function test_approveResult_emitsJobCompleted() public {
        uint256 jobId = _submitted();

        vm.expectEmit(true, true, false, true);
        emit JobCompleted(jobId, provider, PRICE);

        vm.prank(client);
        escrow.approveResult(jobId);
    }

    // ── 중복 정산 차단 ───────────────────────────────────────────

    function test_completedJob_cannotBeSettledTwice() public {
        uint256 jobId = _submitted();

        vm.prank(client);
        escrow.approveResult(jobId);

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not SUBMITTED"));
        escrow.approveResult(jobId);

        assertEq(token.balanceOf(provider), PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    // ── 존재하지 않는 작업 ───────────────────────────────────────

    function test_unknownJob_isNone() public view {
        assertEq(uint8(_state(999)), uint8(GPUJobEscrow.JobState.NONE));
    }

    // ── cancelJob ────────────────────────────────────────────────

    function test_cancelJob_refundsClientAndCancels() public {
        uint256 before = token.balanceOf(client);
        uint256 jobId  = _funded();

        vm.prank(client);
        escrow.cancelJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_cancelJob_rejectsNonClient() public {
        uint256 jobId = _funded();

        vm.prank(stranger);
        vm.expectRevert(bytes("Escrow: not the client"));
        escrow.cancelJob(jobId);
    }

    function test_cancelJob_rejectsBeforeFunding() public {
        uint256 jobId = _created();

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.cancelJob(jobId);
    }

    function test_cancelJob_rejectsAfterAccept() public {
        uint256 jobId = _assigned();

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.cancelJob(jobId);
    }

    /// @dev 취소와 만료 정산은 결과가 같으므로 시한 뒤에도 취소가 열려 있다.
    function test_cancelJob_stillWorksAfterAcceptDeadline() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(client);
        escrow.cancelJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_cancelJob_emitsJobCancelled() public {
        uint256 jobId = _funded();

        vm.expectEmit(true, true, false, true);
        emit JobCancelled(jobId, client, PRICE);

        vm.prank(client);
        escrow.cancelJob(jobId);
    }

    function test_cancelJob_cannotBeCalledTwice() public {
        uint256 jobId = _funded();

        vm.prank(client);
        escrow.cancelJob(jobId);

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.cancelJob(jobId);
    }

    // ── settleTimeout · 만료 전 호출 실패 ────────────────────────

    function test_settleTimeout_rejectsBeforeAcceptDeadline() public {
        uint256 jobId = _funded();

        vm.expectRevert(GPUJobEscrow.DeadlineNotReached.selector);
        escrow.settleTimeout(jobId);
    }

    function test_settleTimeout_rejectsBeforeExecDeadline() public {
        uint256 jobId = _assigned();

        vm.expectRevert(GPUJobEscrow.DeadlineNotReached.selector);
        escrow.settleTimeout(jobId);
    }

    function test_settleTimeout_rejectsBeforeApproveDeadline() public {
        uint256 jobId = _submitted();

        vm.expectRevert(GPUJobEscrow.DeadlineNotReached.selector);
        escrow.settleTimeout(jobId);
    }

    function test_settleTimeout_rejectsOnTerminalState() public {
        uint256 jobId = _submitted();
        vm.prank(client);
        escrow.approveResult(jobId);

        vm.expectRevert(bytes("Escrow: not settleable"));
        escrow.settleTimeout(jobId);
    }

    // ── 경계 · 정각에는 정상 함수만 ──────────────────────────────

    function test_boundary_atDeadline_acceptSucceedsSettleFails() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId));

        vm.expectRevert(GPUJobEscrow.DeadlineNotReached.selector);
        escrow.settleTimeout(jobId);

        vm.prank(provider);
        escrow.acceptJob(jobId);
        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.ASSIGNED));
    }

    function test_boundary_atDeadline_submitSucceedsSettleFails() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId));

        vm.expectRevert(GPUJobEscrow.DeadlineNotReached.selector);
        escrow.settleTimeout(jobId);

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);
        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.SUBMITTED));
    }

    // ── 경계 · 직후에는 타임아웃만 ───────────────────────────────

    function test_boundary_afterDeadline_acceptFailsSettleSucceeds() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(provider);
        vm.expectRevert(GPUJobEscrow.DeadlineExpired.selector);
        escrow.acceptJob(jobId);

        escrow.settleTimeout(jobId);
        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
    }

    function test_boundary_afterDeadline_submitFailsSettleSucceeds() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(provider);
        vm.expectRevert(GPUJobEscrow.DeadlineExpired.selector);
        escrow.submitResult(jobId, RESULT_HASH);

        escrow.settleTimeout(jobId);
        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
    }

    // ── 만료 정산 · 상태별 방향과 잔액 ───────────────────────────

    function test_settleTimeout_fundedRefundsClient() public {
        uint256 before = token.balanceOf(client);
        uint256 jobId  = _funded();
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before);
        assertEq(token.balanceOf(provider), 0);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_settleTimeout_assignedRefundsClient() public {
        uint256 before = token.balanceOf(client);
        uint256 jobId  = _assigned();
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before);
        assertEq(token.balanceOf(provider), 0);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_settleTimeout_submittedPaysProvider() public {
        uint256 before = token.balanceOf(client);
        uint256 jobId  = _submitted();
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.COMPLETED));
        assertEq(token.balanceOf(client), before - PRICE);
        assertEq(token.balanceOf(provider), PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    // ── 호출 권한 ────────────────────────────────────────────────

    /// @dev 당사자가 아닌 제3자가 불러도 결과가 같아야 한다.
    function test_settleTimeout_callableByStranger() public {
        uint256 jobId = _submitted();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(stranger);
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(provider), PRICE);
    }

    /// @dev 호출자는 jobId 만 넘긴다. 수령자·금액·방향을 바꿀 수단이 없다.
    function test_settleTimeout_callerGetsNothing() public {
        uint256 jobId = _submitted();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(stranger);
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(stranger), 0);
        assertEq(token.balanceOf(provider), PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    // ── 만료 이후 정상 함수 차단 ─────────────────────────────────

    function test_afterTimeout_approveResultRejected() public {
        uint256 jobId = _submitted();
        vm.warp(_deadline(jobId) + 1);
        escrow.settleTimeout(jobId);

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not SUBMITTED"));
        escrow.approveResult(jobId);
    }

    function test_afterTimeout_acceptJobRejected() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId) + 1);
        escrow.settleTimeout(jobId);

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.acceptJob(jobId);
    }

    function test_afterTimeout_submitResultRejected() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);
        escrow.settleTimeout(jobId);

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.submitResult(jobId, RESULT_HASH);
    }

    // ── 중복 차단 ────────────────────────────────────────────────

    function test_settleTimeout_cannotBeCalledTwice() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        vm.expectRevert(bytes("Escrow: not settleable"));
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(address(escrow)), 0);
    }

    // ── 이벤트 ───────────────────────────────────────────────────

    function test_settleTimeout_emitsJobTimedOutWithFromState() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        vm.expectEmit(true, false, false, true);
        emit JobTimedOut(jobId, GPUJobEscrow.JobState.ASSIGNED);

        escrow.settleTimeout(jobId);
    }

    function test_settleTimeout_submittedEmitsJobCompleted() public {
        uint256 jobId = _submitted();
        vm.warp(_deadline(jobId) + 1);

        vm.expectEmit(true, true, false, true);
        emit JobCompleted(jobId, provider, PRICE);

        escrow.settleTimeout(jobId);
    }
}
