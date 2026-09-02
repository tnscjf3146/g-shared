// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20Minimal {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// GPU 작업 에스크로 v2
///
/// 온체인에 두는 것은 정산 정보뿐이다.
/// workloadId · imageDigest · datasetHash 같은 작업 상세는 백엔드 DB 가 원본이다.
///
/// 돈이 나가는 함수는 상태를 먼저 바꾸고 나서 토큰을 보낸다 (재진입 방어).
contract GPUJobEscrow {
    enum JobState { NONE, CREATED, FUNDED, ASSIGNED, SUBMITTED, COMPLETED, CANCELLED }

    /// 필드 순서가 저장 슬롯 수를 정한다.
    /// client(20) + state(1) + deadline(8) = 29 바이트라 한 슬롯에 들어간다.
    struct Job {
        address  client;
        JobState state;
        uint64   deadline;     // 상태가 바뀔 때 갱신된다
        address  provider;     // 수락 전에는 address(0)
        uint256  price;
        bytes32  resultHash;
    }

    IERC20Minimal public immutable token;
    uint64 public immutable execDeadline;      // 수락 후 결과 제출까지
    uint64 public immutable approveDeadline;   // 제출 후 의뢰자 승인까지

    uint256 public nextJobId = 1;
    mapping(uint256 => Job) public jobs;

    event JobCreated(uint256 indexed jobId, address indexed client, uint256 price);
    event JobFunded(uint256 indexed jobId, uint256 price);
    event JobCancelled(uint256 indexed jobId, address indexed client, uint256 refund);
    event JobAccepted(uint256 indexed jobId, address indexed provider, uint64 deadline);
    event ResultSubmitted(uint256 indexed jobId, address indexed provider, bytes32 resultHash, uint64 deadline);
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);
    event JobTimedOut(uint256 indexed jobId, JobState fromState);

    constructor(address token_, uint64 execDeadline_, uint64 approveDeadline_) {
        require(token_ != address(0), "Escrow: token is zero");
        require(execDeadline_ > 0 && approveDeadline_ > 0, "Escrow: deadline is zero");
        token = IERC20Minimal(token_);
        execDeadline = execDeadline_;
        approveDeadline = approveDeadline_;
    }

    // ─────────────────────────────────────────────────────────────
    // 의뢰자
    // ─────────────────────────────────────────────────────────────

    /// 작업을 만든다. 아직 돈은 움직이지 않는다.
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

    /// 대금을 예치한다. 의뢰자가 미리 token.approve 를 해두어야 한다.
    function fundJob(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.CREATED, "Escrow: not CREATED");

        job.state = JobState.FUNDED;

        bool ok = token.transferFrom(msg.sender, address(this), job.price);
        require(ok, "Escrow: transferFrom failed");

        emit JobFunded(jobId, job.price);
    }

    /// 수락 전이면 취소한다. 예치했다면 돌려받는다.
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
            bool ok = token.transfer(job.client, refund);
            require(ok, "Escrow: refund failed");
        }

        emit JobCancelled(jobId, job.client, refund);
    }

    /// 결과를 승인하고 정산한다.
    function approveResult(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.SUBMITTED, "Escrow: not SUBMITTED");

        job.state = JobState.COMPLETED;

        bool ok = token.transfer(job.provider, job.price);
        require(ok, "Escrow: payout failed");

        emit JobCompleted(jobId, job.provider, job.price);
    }

    // ─────────────────────────────────────────────────────────────
    // 공급자
    // ─────────────────────────────────────────────────────────────

    /// 일감을 잡는다. 먼저 도착한 트랜잭션이 이긴다.
    /// 늦게 온 트랜잭션은 revert 되며, 이는 오류가 아니라 정상 상황이다.
    function acceptJob(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.state == JobState.FUNDED, "Escrow: not FUNDED");
        require(job.client != msg.sender, "Escrow: client cannot accept");

        job.provider = msg.sender;
        job.state = JobState.ASSIGNED;
        job.deadline = uint64(block.timestamp) + execDeadline;

        emit JobAccepted(jobId, msg.sender, job.deadline);
    }

    /// 결과 해시를 올린다. 해시는 공급자가 직접 계산한다.
    /// 백엔드가 만들면 서명이 백엔드 것이 되어 부인 방지가 사라진다.
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
    // 누구나
    // ─────────────────────────────────────────────────────────────

    /// 시한이 지난 작업을 정리한다. 호출자 제한이 없다.
    /// 컨트랙트가 시한을 직접 검사하므로 누가 눌러도 결과가 같다.
    ///
    ///   ASSIGNED  공급자가 결과를 못 냈다      → 의뢰자 환불
    ///   SUBMITTED 의뢰자가 승인하지 않았다     → 공급자 지급
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

            bool ok = token.transfer(job.client, job.price);
            require(ok, "Escrow: refund failed");

            emit JobCancelled(jobId, job.client, job.price);
        } else {
            job.state = JobState.COMPLETED;

            bool ok = token.transfer(job.provider, job.price);
            require(ok, "Escrow: payout failed");

            emit JobCompleted(jobId, job.provider, job.price);
        }

        emit JobTimedOut(jobId, fromState);
    }
}
