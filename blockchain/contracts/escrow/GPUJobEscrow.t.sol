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
    uint256 internal constant PRICE          = 10 ether; // 10 GSC
    bytes32 internal constant RESULT_HASH    = keccak256("result");

    event JobCreated(uint256 indexed jobId, address indexed client, uint256 price);
    event JobFunded(uint256 indexed jobId, uint256 price);
    event JobAccepted(uint256 indexed jobId, address indexed provider);
    event ResultSubmitted(uint256 indexed jobId, address indexed provider, bytes32 resultHash);
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);

    function setUp() public {
        token  = new GCoin(client);
        escrow = new GPUJobEscrow(address(token));
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
        (, GPUJobEscrow.JobState s, , , ) = escrow.jobs(jobId);
        return s;
    }

    // ── 생성자 ───────────────────────────────────────────────────

    function test_constructor_rejectsZeroToken() public {
        vm.expectRevert(bytes("Escrow: token is zero"));
        new GPUJobEscrow(address(0));
    }

    function test_constructor_storesPaymentToken() public view {
        assertEq(address(escrow.paymentToken()), address(token));
    }

    /// @dev 취소·환불·타임아웃 구현이 끝나면 이 테스트와 컨트랙트의 가드를 함께 지운다.
    function test_constructor_revertsOnSsafyChain() public {
        vm.chainId(SSAFY_CHAIN_ID);

        vm.expectRevert(GPUJobEscrow.SsafyDeploymentBlocked.selector);
        new GPUJobEscrow(address(token));
    }

    function test_constructor_allowsOtherChains() public {
        vm.chainId(31337);

        GPUJobEscrow local = new GPUJobEscrow(address(token));
        assertEq(address(local.paymentToken()), address(token));
    }

    // ── createJob ────────────────────────────────────────────────

    function test_createJob_movesToCreated() public {
        uint256 jobId = _created();

        (address c, GPUJobEscrow.JobState s, address p, uint256 price, bytes32 rh) = escrow.jobs(jobId);
        assertEq(jobId, 1);
        assertEq(c, client);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.CREATED));
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
        emit JobFunded(jobId, PRICE);

        vm.prank(client);
        escrow.fundJob(jobId);
    }

    // ── acceptJob ────────────────────────────────────────────────

    function test_acceptJob_movesToAssignedAndRecordsProvider() public {
        uint256 jobId = _funded();

        vm.prank(provider);
        escrow.acceptJob(jobId);

        (, GPUJobEscrow.JobState s, address p, , ) = escrow.jobs(jobId);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.ASSIGNED));
        assertEq(p, provider);
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
        emit JobAccepted(jobId, provider);

        vm.prank(provider);
        escrow.acceptJob(jobId);
    }

    // ── submitResult ─────────────────────────────────────────────

    function test_submitResult_storesHashAndMovesToSubmitted() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        (, GPUJobEscrow.JobState s, , , bytes32 rh) = escrow.jobs(jobId);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.SUBMITTED));
        assertEq(rh, RESULT_HASH);
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
        emit ResultSubmitted(jobId, provider, RESULT_HASH);

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
}
