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
    address internal provider = address(0x9401DE);   // 정산 주소. 사람 지갑
    address internal operator = address(0x09E4A70);  // 서명 대행자. 기계 키
    address internal operator2 = address(0x09E4A72); // 같은 사람의 다른 기계
    address internal stranger = address(0x57A9E7);

    uint64  internal constant ACCEPT_TIMEOUT    = 2 hours;
    uint64  internal constant EXECUTION_TIMEOUT = 1 hours;
    uint256 internal constant PRICE            = 10 ether; // 10 GSC
    bytes32 internal constant RESULT_HASH      = keccak256("result");
    bytes32 internal constant EXTERNAL_REF     = keccak256("job-42");

    event JobFunded(
        uint256 indexed jobId,
        bytes32 indexed externalRef,
        address indexed provider,
        address client,
        uint256 price,
        uint64  deadline
    );
    event JobAccepted(
        uint256 indexed jobId,
        address indexed provider,
        address indexed operator,
        uint64  deadline
    );
    event ResultSubmitted(uint256 indexed jobId, address indexed operator, bytes32 resultHash);
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);
    event JobCancelled(uint256 indexed jobId, address indexed client, uint256 refund);
    event JobAborted(uint256 indexed jobId, address indexed operator);
    event OperatorUpdated(address indexed provider, address indexed operator, bool allowed);
    event JobTimedOut(uint256 indexed jobId, GPUJobEscrow.JobState fromState);

    function setUp() public {
        token  = new GCoin(client);
        escrow = new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT);
    }

    // ── 보조 함수 ────────────────────────────────────────────────
    //
    // 상태가 둘로 줄어 계단도 둘이다. COMPLETED 와 CANCELLED 는 종료 상태라
    // 그 지점에서 시작하는 테스트가 없다.

    function _funded() internal returns (uint256 jobId) {
        vm.prank(client);
        token.approve(address(escrow), PRICE);
        vm.prank(client);
        jobId = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
    }

    function _assigned() internal returns (uint256 jobId) {
        jobId = _funded();
        vm.prank(provider);
        escrow.acceptJob(jobId);
    }

    function _state(uint256 jobId) internal view returns (GPUJobEscrow.JobState) {
        (, GPUJobEscrow.JobState s, , , , , ) = escrow.jobs(jobId);
        return s;
    }

    function _deadline(uint256 jobId) internal view returns (uint64) {
        (, , uint64 dl, , , , ) = escrow.jobs(jobId);
        return dl;
    }

    function _provider(uint256 jobId) internal view returns (address) {
        (, , , address p, , , ) = escrow.jobs(jobId);
        return p;
    }

    function _operator(uint256 jobId) internal view returns (address) {
        (, , , , address op, , ) = escrow.jobs(jobId);
        return op;
    }

    // ── 생성자 ───────────────────────────────────────────────────

    function test_constructor_rejectsZeroToken() public {
        vm.expectRevert(bytes("Escrow: token is zero"));
        new GPUJobEscrow(address(0), ACCEPT_TIMEOUT, EXECUTION_TIMEOUT);
    }

    function test_constructor_storesPaymentToken() public view {
        assertEq(address(escrow.paymentToken()), address(token));
        assertEq(escrow.acceptTimeout(), ACCEPT_TIMEOUT);
        assertEq(escrow.executionTimeout(), EXECUTION_TIMEOUT);
        assertEq(escrow.nextJobId(), 1);
    }

    function test_constructor_rejectsZeroDeadline() public {
        vm.expectRevert(bytes("Escrow: deadline is zero"));
        new GPUJobEscrow(address(token), 0, EXECUTION_TIMEOUT);

        vm.expectRevert(bytes("Escrow: deadline is zero"));
        new GPUJobEscrow(address(token), ACCEPT_TIMEOUT, 0);
    }

    // ── createAndFundJob ─────────────────────────────────────────
    //
    // 생성과 예치가 한 트랜잭션이라 대금 없는 작업이 존재하지 않는다.

    function test_createAndFundJob_movesTokensAndFunds() public {
        uint256 before = token.balanceOf(client);
        uint256 jobId  = _funded();

        assertEq(jobId, 1);
        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.FUNDED));
        assertEq(token.balanceOf(client), before - PRICE);
        assertEq(token.balanceOf(address(escrow)), PRICE);
        assertEq(escrow.nextJobId(), 2);
    }

    function test_createAndFundJob_storesJobFields() public {
        uint256 jobId = _funded();

        (
            address c,
            GPUJobEscrow.JobState s,
            uint64 dl,
            address p,
            address op,
            uint256 price,
            bytes32 hash
        ) = escrow.jobs(jobId);

        assertEq(c, client);
        assertEq(uint8(s), uint8(GPUJobEscrow.JobState.FUNDED));
        assertEq(dl, uint64(block.timestamp) + ACCEPT_TIMEOUT);
        assertEq(p, provider);
        assertEq(op, address(0)); // operator 는 수락 시점에 정해진다
        assertEq(price, PRICE);
        assertEq(hash, bytes32(0));
    }

    function test_createAndFundJob_rejectsZeroPrice() public {
        vm.prank(client);
        vm.expectRevert(bytes("Escrow: price is zero"));
        escrow.createAndFundJob(EXTERNAL_REF, 0, provider);
    }

    /// @notice 승인 없이 부르면 토큰 전송이 실패하고 작업 생성까지 되돌아간다.
    function test_createAndFundJob_failsWithoutApprove() public {
        vm.prank(client);
        vm.expectRevert();
        escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);

        assertEq(escrow.nextJobId(), 1);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    /// @notice 영주소를 지정하면 수락할 주체가 없어 자금이 시한까지 묶인다. 미리 막는다.
    function test_createAndFundJob_rejectsZeroProvider() public {
        vm.prank(client);
        token.approve(address(escrow), PRICE);

        vm.prank(client);
        vm.expectRevert(GPUJobEscrow.InvalidProvider.selector);
        escrow.createAndFundJob(EXTERNAL_REF, PRICE, address(0));

        assertEq(escrow.nextJobId(), 1);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    /// @notice 자기 자신을 지정하면 스스로 수락하고 제출해 대금을 되가져가는 경로가 된다.
    function test_createAndFundJob_rejectsClientAsProvider() public {
        vm.prank(client);
        token.approve(address(escrow), PRICE);

        vm.prank(client);
        vm.expectRevert(GPUJobEscrow.InvalidProvider.selector);
        escrow.createAndFundJob(EXTERNAL_REF, PRICE, client);

        assertEq(escrow.nextJobId(), 1);
    }

    function test_createAndFundJob_emitsJobFunded() public {
        vm.prank(client);
        token.approve(address(escrow), PRICE);

        vm.expectEmit(true, true, true, true);
        emit JobFunded(1, EXTERNAL_REF, provider, client, PRICE, uint64(block.timestamp) + ACCEPT_TIMEOUT);

        vm.prank(client);
        escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
    }

    /// @notice 작업 번호는 컨트랙트가 매긴다. externalRef 는 참조값일 뿐 키가 아니다.
    function test_createAndFundJob_sameExternalRefIsAllowed() public {
        vm.startPrank(client);
        token.approve(address(escrow), PRICE * 2);
        uint256 first  = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        uint256 second = escrow.createAndFundJob(EXTERNAL_REF, PRICE, provider);
        vm.stopPrank();

        assertEq(first, 1);
        assertEq(second, 2);
    }

    // ── setOperator ──────────────────────────────────────────────
    //
    // 정산 주소 본인이 자기 대신 서명할 기계를 승인한다. 승인하지 않으면
    // 본인만 부를 수 있어 기본값이 안전하다.

    function test_setOperator_grantsAndRevokes() public {
        assertFalse(escrow.isOperator(provider, operator));

        vm.prank(provider);
        escrow.setOperator(operator, true);
        assertTrue(escrow.isOperator(provider, operator));

        vm.prank(provider);
        escrow.setOperator(operator, false);
        assertFalse(escrow.isOperator(provider, operator));
    }

    function test_setOperator_rejectsZeroAddress() public {
        vm.prank(provider);
        vm.expectRevert(GPUJobEscrow.InvalidOperator.selector);
        escrow.setOperator(address(0), true);
    }

    /// @notice 자기 목록만 고칠 수 있다. 남의 대행자를 등록할 수 없다.
    function test_setOperator_onlyAffectsCallerOwnList() public {
        vm.prank(stranger);
        escrow.setOperator(operator, true);

        assertTrue(escrow.isOperator(stranger, operator));
        assertFalse(escrow.isOperator(provider, operator));
    }

    function test_setOperator_emitsOperatorUpdated() public {
        vm.expectEmit(true, true, false, true);
        emit OperatorUpdated(provider, operator, true);

        vm.prank(provider);
        escrow.setOperator(operator, true);
    }

    /// @notice 한 사람이 기계를 여럿 승인할 수 있다. User 1:N Worker 구조를 담는다.
    function test_setOperator_supportsMultipleMachines() public {
        vm.startPrank(provider);
        escrow.setOperator(operator, true);
        escrow.setOperator(operator2, true);
        vm.stopPrank();

        assertTrue(escrow.isOperator(provider, operator));
        assertTrue(escrow.isOperator(provider, operator2));
    }

    // ── acceptJob · 권한 ─────────────────────────────────────────

    /// @notice 승인된 대행자가 수락하면 job.operator 에 그 주소가 남는다.
    function test_acceptJob_byApprovedOperator() public {
        vm.prank(provider);
        escrow.setOperator(operator, true);

        uint256 jobId = _funded();
        vm.prank(operator);
        escrow.acceptJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.ASSIGNED));
        assertEq(_operator(jobId), operator);
        assertEq(_provider(jobId), provider);
    }

    /// @notice 정산 주소 본인이 직접 수락하면 자기 주소가 operator 로 기록된다.
    function test_acceptJob_byProviderRecordsSelfAsOperator() public {
        uint256 jobId = _funded();
        vm.prank(provider);
        escrow.acceptJob(jobId);

        assertEq(_operator(jobId), provider);
    }

    /// @notice 승인받지 않은 기계는 수락할 수 없다.
    function test_acceptJob_rejectsUnapprovedOperator() public {
        uint256 jobId = _funded();

        vm.prank(operator);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.acceptJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.FUNDED));
    }

    /// @notice 다른 사람이 승인한 기계라도 이 작업에는 권한이 없다.
    function test_acceptJob_rejectsOperatorApprovedByAnotherProvider() public {
        vm.prank(stranger);
        escrow.setOperator(operator, true);

        uint256 jobId = _funded();
        vm.prank(operator);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.acceptJob(jobId);
    }

    function test_acceptJob_emitsOperatorInEvent() public {
        vm.prank(provider);
        escrow.setOperator(operator, true);

        uint256 jobId = _funded();

        vm.expectEmit(true, true, true, true);
        emit JobAccepted(jobId, provider, operator, uint64(block.timestamp) + EXECUTION_TIMEOUT);

        vm.prank(operator);
        escrow.acceptJob(jobId);
    }

    // ── 수락 이후 권한 ───────────────────────────────────────────
    //
    // 승인 검사는 수락 시점에만 한다. 그 뒤에는 확정된 operator 본인인지만 본다.

    /// @notice 승인을 취소해도 이미 수락한 작업은 끝까지 간다.
    ///         12시간 학습 중에 실수로 승인을 취소해 대금을 잃는 일을 막는다.
    function test_revokedOperatorCanStillFinishOngoingJob() public {
        vm.prank(provider);
        escrow.setOperator(operator, true);

        uint256 jobId = _funded();
        vm.prank(operator);
        escrow.acceptJob(jobId);

        // 사용자가 승인을 취소한다
        vm.prank(provider);
        escrow.setOperator(operator, false);

        uint256 before = token.balanceOf(provider);
        vm.prank(operator);
        escrow.submitResult(jobId, RESULT_HASH);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.COMPLETED));
        assertEq(token.balanceOf(provider), before + PRICE);
    }

    /// @notice 기계 둘을 승인한 상태에서 하나만 취소해도 나머지는 그대로 동작한다.
    ///         취소된 기계의 진행 중 작업도 끝까지 간다.
    function test_revokingOneOperatorDoesNotAffectTheOther() public {
        vm.startPrank(provider);
        escrow.setOperator(operator, true);
        escrow.setOperator(operator2, true);
        vm.stopPrank();

        // 기계 둘이 각각 다른 작업을 맡는다
        uint256 first = _funded();
        vm.prank(operator);
        escrow.acceptJob(first);

        uint256 second = _funded();
        vm.prank(operator2);
        escrow.acceptJob(second);

        // 첫째 기계의 승인만 취소한다
        vm.prank(provider);
        escrow.setOperator(operator, false);
        assertFalse(escrow.isOperator(provider, operator));
        assertTrue(escrow.isOperator(provider, operator2));

        // 취소된 기계도 이미 맡은 작업은 끝낸다
        vm.prank(operator);
        escrow.submitResult(first, RESULT_HASH);
        assertEq(uint8(_state(first)), uint8(GPUJobEscrow.JobState.COMPLETED));

        // 남은 기계도 영향을 받지 않는다
        vm.prank(operator2);
        escrow.submitResult(second, RESULT_HASH);
        assertEq(uint8(_state(second)), uint8(GPUJobEscrow.JobState.COMPLETED));

        assertEq(token.balanceOf(provider), PRICE * 2);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    /// @notice 취소된 기계는 새 작업을 받을 수 없다.
    function test_revokedOperatorCannotAcceptNewJob() public {
        vm.prank(provider);
        escrow.setOperator(operator, true);
        vm.prank(provider);
        escrow.setOperator(operator, false);

        uint256 jobId = _funded();
        vm.prank(operator);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.acceptJob(jobId);
    }

    /// @notice 승인된 다른 기계도 남이 맡은 작업에는 끼어들 수 없다.
    function test_submitResult_rejectsAnotherApprovedOperator() public {
        vm.startPrank(provider);
        escrow.setOperator(operator, true);
        escrow.setOperator(operator2, true);
        vm.stopPrank();

        uint256 jobId = _funded();
        vm.prank(operator);
        escrow.acceptJob(jobId);

        vm.prank(operator2);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.submitResult(jobId, RESULT_HASH);
    }

    function test_abortJob_rejectsAnotherApprovedOperator() public {
        vm.startPrank(provider);
        escrow.setOperator(operator, true);
        escrow.setOperator(operator2, true);
        vm.stopPrank();

        uint256 jobId = _funded();
        vm.prank(operator);
        escrow.acceptJob(jobId);

        vm.prank(operator2);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.abortJob(jobId);
    }

    /// @notice 정산 주소 본인이라도 대행자가 맡은 작업을 대신 제출할 수 없다.
    function test_submitResult_rejectsProviderWhenOperatorAccepted() public {
        vm.prank(provider);
        escrow.setOperator(operator, true);

        uint256 jobId = _funded();
        vm.prank(operator);
        escrow.acceptJob(jobId);

        vm.prank(provider);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.submitResult(jobId, RESULT_HASH);
    }

    /// @notice 대금은 언제나 정산 주소로 간다. 대행자에게 쌓이지 않는다.
    function test_submitResult_paysProviderNotOperator() public {
        vm.prank(provider);
        escrow.setOperator(operator, true);

        uint256 jobId = _funded();
        vm.prank(operator);
        escrow.acceptJob(jobId);

        vm.prank(operator);
        escrow.submitResult(jobId, RESULT_HASH);

        assertEq(token.balanceOf(provider), PRICE);
        assertEq(token.balanceOf(operator), 0);
    }

    // ── acceptJob ────────────────────────────────────────────────

    function test_acceptJob_movesToAssigned() public {
        uint256 jobId = _funded();

        vm.prank(provider);
        escrow.acceptJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.ASSIGNED));
        assertEq(_deadline(jobId), uint64(block.timestamp) + EXECUTION_TIMEOUT);
    }

    function test_acceptJob_rejectsUnknownJob() public {
        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.acceptJob(999);
    }

    function test_acceptJob_rejectsSecondAccept() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.acceptJob(jobId);
    }

    /// @notice 의뢰자는 자신을 지정할 수 없으므로 수락 자격도 없다.
    function test_acceptJob_rejectsClient() public {
        uint256 jobId = _funded();

        vm.prank(client);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.acceptJob(jobId);
    }

    /// @notice 지정되지 않은 주소는 수락할 수 없다. 선점으로 작업을 가로챌 수 없다는 뜻이다.
    function test_acceptJob_rejectsUndesignatedProvider() public {
        uint256 jobId = _funded();

        vm.prank(stranger);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.acceptJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.FUNDED));
    }

    /// @notice 지정되지 않은 주소가 먼저 시도해도 지정된 공급자가 그대로 수락할 수 있다.
    function test_acceptJob_designatedProviderStillSucceedsAfterAttempt() public {
        uint256 jobId = _funded();

        vm.prank(stranger);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.acceptJob(jobId);

        vm.prank(provider);
        escrow.acceptJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.ASSIGNED));
    }

    function test_acceptJob_emitsJobAccepted() public {
        uint256 jobId = _funded();

        vm.expectEmit(true, true, true, true);
        emit JobAccepted(jobId, provider, provider, uint64(block.timestamp) + EXECUTION_TIMEOUT);

        vm.prank(provider);
        escrow.acceptJob(jobId);
    }

    // ── submitResult ─────────────────────────────────────────────
    //
    // 제출과 정산이 한 트랜잭션에서 끝난다. 승인 단계가 없다.

    function test_submitResult_paysProviderAndCompletes() public {
        uint256 jobId  = _assigned();
        uint256 before = token.balanceOf(provider);

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.COMPLETED));
        assertEq(token.balanceOf(provider), before + PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_submitResult_storesHash() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        (, , , , , , bytes32 hash) = escrow.jobs(jobId);
        assertEq(hash, RESULT_HASH);
    }

    function test_submitResult_rejectsNonProvider() public {
        uint256 jobId = _assigned();

        vm.prank(stranger);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.submitResult(jobId, RESULT_HASH);
    }

    function test_submitResult_rejectsEmptyHash() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: empty result hash"));
        escrow.submitResult(jobId, bytes32(0));
    }

    function test_submitResult_rejectsWhenNotAssigned() public {
        uint256 jobId = _funded();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.submitResult(jobId, RESULT_HASH);
    }

    function test_submitResult_cannotBeCalledTwice() public {
        uint256 jobId = _assigned();

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.submitResult(jobId, RESULT_HASH);

        assertEq(token.balanceOf(address(escrow)), 0);
    }

    /// @notice 제출과 완료가 같은 트랜잭션에서 함께 나간다. 백엔드가 둘을 함께 받는다.
    function test_submitResult_emitsBothEvents() public {
        uint256 jobId = _assigned();

        vm.expectEmit(true, true, false, true);
        emit ResultSubmitted(jobId, provider, RESULT_HASH);
        vm.expectEmit(true, true, false, true);
        emit JobCompleted(jobId, provider, PRICE);

        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);
    }

    // ── abortJob ─────────────────────────────────────────────────
    //
    // 공급자가 계속할 수 없을 때 스스로 반납한다. 시한을 기다리지 않는다.

    function test_abortJob_refundsClientAndCancels() public {
        uint256 jobId  = _assigned();
        uint256 before = token.balanceOf(client);

        vm.prank(provider);
        escrow.abortJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before + PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_abortJob_rejectsNonProvider() public {
        uint256 jobId = _assigned();

        vm.prank(stranger);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.abortJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.ASSIGNED));
    }

    function test_abortJob_rejectsClient() public {
        uint256 jobId = _assigned();

        vm.prank(client);
        vm.expectRevert(GPUJobEscrow.NotAuthorizedOperator.selector);
        escrow.abortJob(jobId);
    }

    function test_abortJob_rejectsWhenFunded() public {
        uint256 jobId = _funded();

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.abortJob(jobId);
    }

    function test_abortJob_rejectsWhenCompleted() public {
        uint256 jobId = _assigned();
        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.abortJob(jobId);
    }

    function test_abortJob_rejectsWhenCancelled() public {
        uint256 jobId = _assigned();
        vm.prank(provider);
        escrow.abortJob(jobId);

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.abortJob(jobId);
    }

    function test_abortJob_cannotBeCalledTwice() public {
        uint256 jobId  = _assigned();
        uint256 before = token.balanceOf(client);

        vm.prank(provider);
        escrow.abortJob(jobId);

        vm.prank(provider);
        vm.expectRevert(bytes("Escrow: not ASSIGNED"));
        escrow.abortJob(jobId);

        assertEq(token.balanceOf(client), before + PRICE);
    }

    /// @notice 시한 검사를 두지 않는다. 만료 뒤에도 settleTimeout 과 결과가 같아 무해하다.
    function test_abortJob_worksAfterDeadline() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        uint256 before = token.balanceOf(client);
        vm.prank(provider);
        escrow.abortJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before + PRICE);
    }

    /// @notice 만료 뒤 abortJob 과 settleTimeout 이 겹쳐도 먼저 실행된 쪽만 성공한다.
    function test_abortJob_racesSafelyWithSettleTimeout() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(provider);
        escrow.abortJob(jobId);

        vm.expectRevert(bytes("Escrow: not settleable"));
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_abortJob_emitsBothEvents() public {
        uint256 jobId = _assigned();

        vm.expectEmit(true, true, false, true);
        emit JobCancelled(jobId, client, PRICE);
        vm.expectEmit(true, true, false, true);
        emit JobAborted(jobId, provider);

        vm.prank(provider);
        escrow.abortJob(jobId);
    }

    // ── cancelJob ────────────────────────────────────────────────

    function test_cancelJob_refundsClientAndCancels() public {
        uint256 jobId  = _funded();
        uint256 before = token.balanceOf(client);

        vm.prank(client);
        escrow.cancelJob(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before + PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_cancelJob_rejectsNonClient() public {
        uint256 jobId = _funded();

        vm.prank(stranger);
        vm.expectRevert(bytes("Escrow: not the client"));
        escrow.cancelJob(jobId);
    }

    function test_cancelJob_rejectsAfterAccept() public {
        uint256 jobId = _assigned();

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.cancelJob(jobId);
    }

    /// @notice 수락 시한이 지난 뒤에도 취소할 수 있다. settleTimeout 과 결과가 같다.
    function test_cancelJob_stillWorksAfterAcceptDeadline() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId) + 1);

        uint256 before = token.balanceOf(client);
        vm.prank(client);
        escrow.cancelJob(jobId);

        assertEq(token.balanceOf(client), before + PRICE);
    }

    function test_cancelJob_emitsJobCancelled() public {
        uint256 jobId = _funded();

        vm.expectEmit(true, true, false, true);
        emit JobCancelled(jobId, client, PRICE);

        vm.prank(client);
        escrow.cancelJob(jobId);
    }

    function test_cancelJob_cannotBeCalledTwice() public {
        uint256 jobId  = _funded();
        uint256 before = token.balanceOf(client);

        vm.prank(client);
        escrow.cancelJob(jobId);

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.cancelJob(jobId);

        assertEq(token.balanceOf(client), before + PRICE);
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

    function test_settleTimeout_rejectsOnTerminalState() public {
        uint256 jobId = _assigned();
        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        vm.warp(block.timestamp + 365 days);
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
        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.COMPLETED));
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

    // ── 만료 정산 · 환불 전용 ────────────────────────────────────
    //
    // 지급 경로가 submitResult 하나로 모여 settleTimeout 은 환불만 한다.

    function test_settleTimeout_fundedRefundsClient() public {
        uint256 jobId  = _funded();
        uint256 before = token.balanceOf(client);
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before + PRICE);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_settleTimeout_assignedRefundsClient() public {
        uint256 jobId  = _assigned();
        uint256 before = token.balanceOf(client);
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        assertEq(uint8(_state(jobId)), uint8(GPUJobEscrow.JobState.CANCELLED));
        assertEq(token.balanceOf(client), before + PRICE);
        assertEq(token.balanceOf(provider), 0);
    }

    // ── 호출 권한 ────────────────────────────────────────────────

    function test_settleTimeout_callableByStranger() public {
        uint256 jobId  = _funded();
        uint256 before = token.balanceOf(client);
        vm.warp(_deadline(jobId) + 1);

        vm.prank(stranger);
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(client), before + PRICE);
    }

    /// @notice 호출자는 수령자가 아니다. 수령자와 금액은 컨트랙트가 상태로 결정한다.
    function test_settleTimeout_callerGetsNothing() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(stranger);
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(stranger), 0);
    }

    function test_settleTimeout_cannotBeCalledTwice() public {
        uint256 jobId  = _funded();
        uint256 before = token.balanceOf(client);
        vm.warp(_deadline(jobId) + 1);

        escrow.settleTimeout(jobId);

        vm.expectRevert(bytes("Escrow: not settleable"));
        escrow.settleTimeout(jobId);

        assertEq(token.balanceOf(client), before + PRICE);
    }

    // ── 만료 이후 정상 함수 차단 ─────────────────────────────────

    function test_afterTimeout_acceptJobRejected() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(provider);
        vm.expectRevert(GPUJobEscrow.DeadlineExpired.selector);
        escrow.acceptJob(jobId);
    }

    function test_afterTimeout_submitResultRejected() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        vm.prank(provider);
        vm.expectRevert(GPUJobEscrow.DeadlineExpired.selector);
        escrow.submitResult(jobId, RESULT_HASH);

        assertEq(token.balanceOf(address(escrow)), PRICE);
    }

    // ── 이벤트 ───────────────────────────────────────────────────

    function test_settleTimeout_emitsJobTimedOutWithFromState() public {
        uint256 jobId = _assigned();
        vm.warp(_deadline(jobId) + 1);

        vm.expectEmit(true, false, false, true);
        emit JobTimedOut(jobId, GPUJobEscrow.JobState.ASSIGNED);

        escrow.settleTimeout(jobId);
    }

    function test_settleTimeout_emitsJobCancelled() public {
        uint256 jobId = _funded();
        vm.warp(_deadline(jobId) + 1);

        vm.expectEmit(true, true, false, true);
        emit JobCancelled(jobId, client, PRICE);

        escrow.settleTimeout(jobId);
    }

    // ── 존재하지 않는 작업 ───────────────────────────────────────

    function test_unknownJob_isNone() public view {
        assertEq(uint8(_state(999)), uint8(GPUJobEscrow.JobState.NONE));
        assertEq(_provider(999), address(0));
    }

    // ── 중복 정산 차단 ───────────────────────────────────────────

    /// @notice 완료된 작업은 어느 경로로도 다시 정산되지 않는다.
    function test_completedJob_cannotBeSettledTwice() public {
        uint256 jobId = _assigned();
        vm.prank(provider);
        escrow.submitResult(jobId, RESULT_HASH);

        uint256 providerBalance = token.balanceOf(provider);
        vm.warp(block.timestamp + 365 days);

        vm.expectRevert(bytes("Escrow: not settleable"));
        escrow.settleTimeout(jobId);

        vm.prank(client);
        vm.expectRevert(bytes("Escrow: not FUNDED"));
        escrow.cancelJob(jobId);

        assertEq(token.balanceOf(provider), providerBalance);
        assertEq(token.balanceOf(address(escrow)), 0);
    }
}
