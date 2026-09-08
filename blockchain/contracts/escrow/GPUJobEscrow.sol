// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title GPU 작업 에스크로 컨트랙트 v2
/// @notice GPU 작업에 대한 대금 예치 및 정산을 온체인에서 안전하게 처리합니다.
/// @dev 온체인에는 정산 정보만 보관하며, workloadId, imageDigest, datasetHash 등 작업 상세 정보는 오프체인(백엔드 DB)에 저장됩니다.
/// 또한 재진입(Reentrancy) 공격을 방어하기 위해 CEI(Checks-Effects-Interactions) 패턴을 준수하여 상태 변경 후 토큰을 전송합니다.
contract GPUJobEscrow {
    /// @notice 에스크로 작업의 생명주기를 나타내는 상태 값
    enum JobState { NONE, CREATED, FUNDED, ASSIGNED, SUBMITTED, COMPLETED, CANCELLED }

    /// @notice 단일 작업(Job)에 대한 구조체
    /// @dev 변수 배치를 통해 단일 스토리지 슬롯(32바이트)에 맞춤: client(20) + state(1) + deadline(8) = 29 bytes.
    struct Job {
        address  client;       // 의뢰자 지갑 주소
        JobState state;        // 현재 작업 상태
        uint64   deadline;     // 상태 변경 시마다 갱신되는 제한 시간(타임스탬프)
        address  provider;     // 작업을 수락한 제공자 (수락 전에는 address(0))
        uint256  price;        // 작업 대금 (결제 토큰 최소 단위)
        bytes32  resultHash;   // 결과물 검증을 위한 해시값
    }

    /// @notice 정산에 사용될 ERC20 토큰 컨트랙트
    IERC20 public immutable paymentToken;
    
    /// @notice 작업 수락 후 결과 제출까지 허용되는 최대 시간(초)
    uint64 public immutable execDeadline;
    
    /// @notice 결과 제출 후 의뢰자의 승인까지 허용되는 최대 시간(초)
    uint64 public immutable approveDeadline;

    /// @notice 다음 작업에 부여될 고유 ID
    uint256 public nextJobId = 1;
    
    /// @notice Job ID와 작업 상세 정보 매핑
    mapping(uint256 => Job) public jobs;

    // ─────────────────────────────────────────────────────────────
    // 이벤트(Events)
    // ─────────────────────────────────────────────────────────────

    /// @notice 새로운 작업이 생성되었을 때 발생
    event JobCreated(uint256 indexed jobId, address indexed client, uint256 price);
    /// @notice 작업 대금이 에스크로에 예치되었을 때 발생
    event JobFunded(uint256 indexed jobId, uint256 price);
    /// @notice 작업이 취소되어 환불되었을 때 발생
    event JobCancelled(uint256 indexed jobId, address indexed client, uint256 refund);
    /// @notice 제공자가 작업을 수락했을 때 발생
    event JobAccepted(uint256 indexed jobId, address indexed provider, uint64 deadline);
    /// @notice 제공자가 작업 결과를 제출했을 때 발생
    event ResultSubmitted(uint256 indexed jobId, address indexed provider, bytes32 resultHash, uint64 deadline);
    /// @notice 작업이 완료되고 대금이 지급되었을 때 발생
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);
    /// @notice 제한 시간 초과로 작업 상태가 강제로 정리되었을 때 발생
    event JobTimedOut(uint256 indexed jobId, JobState fromState);

    /// @notice 에스크로 컨트랙트를 초기화합니다.
    /// @param paymentToken_ 에스크로에 사용할 결제 토큰 주소
    /// @param execDeadline_ 작업 수락 후 제출까지의 기한(초)
    /// @param approveDeadline_ 제출 후 의뢰자 승인까지의 기한(초)
    constructor(address paymentToken_, uint64 execDeadline_, uint64 approveDeadline_) {
        require(paymentToken_ != address(0), "Escrow: token is zero");
        require(execDeadline_ > 0 && approveDeadline_ > 0, "Escrow: deadline is zero");
        paymentToken = IERC20(paymentToken_);
        execDeadline = execDeadline_;
        approveDeadline = approveDeadline_;
    }

    // ─────────────────────────────────────────────────────────────
    // 의뢰자(Client) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 새로운 작업을 생성합니다 (토큰 이동 없음).
    /// @param price 지급할 작업 대금
    /// @return jobId 생성된 작업의 고유 ID
    function createJob(uint256 price) external returns (uint256 jobId) {
        require(price > 0, "Escrow: price is zero");

        jobId = nextJobId++;

        jobs[jobId] = Job({
            client:     msg.sender,
            state:      JobState.CREATED,
            deadline:   0,
            provider:   address(0),
            price:      price,
            resultHash: bytes32(0)
        });

        emit JobCreated(jobId, msg.sender, price);
    }

    /// @notice 작업 대금을 에스크로 컨트랙트에 예치합니다.
    /// @dev 호출 전에 반드시 paymentToken.approve를 통해 컨트랙트가 대금을 출금할 수 있도록 허용해야 합니다.
    /// @param jobId 대금을 예치할 작업 ID
    function fundJob(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.CREATED, "Escrow: not CREATED");

        job.state = JobState.FUNDED;

        bool ok = paymentToken.transferFrom(msg.sender, address(this), job.price);
        require(ok, "Escrow: transferFrom failed");

        emit JobFunded(jobId, job.price);
    }

    /// @notice 아직 수락되지 않은 작업을 취소합니다.
    /// @dev 대금이 예치(FUNDED)된 상태라면 의뢰자에게 환불됩니다.
    /// @param jobId 취소할 작업 ID
    function cancelJob(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(
            job.state == JobState.CREATED || job.state == JobState.FUNDED,
            "Escrow: not cancellable"
        );

        bool wasFunded = job.state == JobState.FUNDED;
        uint256 refund = wasFunded ? job.price : 0;

        job.state = JobState.CANCELLED;

        if (wasFunded) {
            bool ok = paymentToken.transfer(job.client, refund);
            require(ok, "Escrow: refund failed");
        }

        emit JobCancelled(jobId, job.client, refund);
    }

    /// @notice 제출된 결과물을 승인하고 대금을 제공자에게 지급합니다.
    /// @param jobId 승인할 작업 ID
    function approveResult(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.SUBMITTED, "Escrow: not SUBMITTED");

        job.state = JobState.COMPLETED;

        bool ok = paymentToken.transfer(job.provider, job.price);
        require(ok, "Escrow: payout failed");

        emit JobCompleted(jobId, job.provider, job.price);
    }

    // ─────────────────────────────────────────────────────────────
    // 공급자(Provider) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 예치된 일감을 수락하여 작업을 배정받습니다.
    /// @dev 선착순으로 처리되며, 늦게 도착한 트랜잭션은 revert 처리됩니다 (정상 상황).
    /// @param jobId 수락할 작업 ID
    function acceptJob(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.state == JobState.FUNDED, "Escrow: not FUNDED");
        require(job.client != msg.sender, "Escrow: client cannot accept");

        job.provider = msg.sender;
        job.state = JobState.ASSIGNED;
        job.deadline = uint64(block.timestamp) + execDeadline;

        emit JobAccepted(jobId, msg.sender, job.deadline);
    }

    /// @notice 작업이 완료되었음을 알리고 결과물에 대한 해시를 등록합니다.
    /// @dev 백엔드가 서명하지 않고 공급자가 직접 트랜잭션을 발생시켜 부인 방지(Non-repudiation)를 달성합니다.
    /// @param jobId 결과물을 제출할 작업 ID
    /// @param resultHash 결과물에 대한 해시값
    function submitResult(uint256 jobId, bytes32 resultHash) external {
        Job storage job = jobs[jobId];

        require(job.provider == msg.sender, "Escrow: not the provider");
        require(job.state == JobState.ASSIGNED, "Escrow: not ASSIGNED");
        require(resultHash != bytes32(0), "Escrow: empty result hash");

        job.resultHash = resultHash;
        job.state = JobState.SUBMITTED;
        job.deadline = uint64(block.timestamp) + approveDeadline;

        emit ResultSubmitted(jobId, msg.sender, resultHash, job.deadline);
    }

    // ─────────────────────────────────────────────────────────────
    // 공용(Public/Anyone) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 지정된 제한 시간이 지난 작업을 정리합니다 (누구나 호출 가능).
    /// @dev ASSIGNED(제출 시한 초과) -> 의뢰자에게 환불 / SUBMITTED(승인 시한 초과) -> 공급자에게 강제 지급
    /// @param jobId 정리할 작업 ID
    function settleTimeout(uint256 jobId) external {
        Job storage job = jobs[jobId];
        JobState fromState = job.state;

        require(
            fromState == JobState.ASSIGNED || fromState == JobState.SUBMITTED,
            "Escrow: not settleable"
        );
        require(block.timestamp >= job.deadline, "Escrow: deadline not reached");

        if (fromState == JobState.ASSIGNED) {
            job.state = JobState.CANCELLED;

            bool ok = paymentToken.transfer(job.client, job.price);
            require(ok, "Escrow: refund failed");

            emit JobCancelled(jobId, job.client, job.price);
        } else {
            job.state = JobState.COMPLETED;

            bool ok = paymentToken.transfer(job.provider, job.price);
            require(ok, "Escrow: payout failed");

            emit JobCompleted(jobId, job.provider, job.price);
        }

        emit JobTimedOut(jobId, fromState);
    }
}
