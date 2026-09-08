// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title GPU 작업 에스크로 컨트랙트 v2
/// @notice GPU 작업에 대한 대금 예치 및 정산을 온체인에서 안전하게 처리합니다.
///
/// @dev !!! 이 버전은 SSAFY 체인에 배포할 수 없습니다 !!!
/// 예치금이 빠져나가는 경로가 approveResult 하나뿐입니다. 아래 세 경우에 대금이 영구히 잠깁니다.
///   1. FUNDED    - 아무도 수락하지 않는다
///   2. ASSIGNED  - 공급자가 결과를 제출하지 않는다
///   3. SUBMITTED - 의뢰자가 승인하지 않는다
/// 그래서 생성자가 chainid 를 검사해 SSAFY 체인(31221) 배포를 되돌립니다.
/// 취소·환불·타임아웃을 구현하면 SSAFY_CHAIN_ID 상수와 가드, 그리고 해당 테스트를 함께 제거합니다.
/// 범위 축소 근거는 blockchain-skeleton.md 5절이며, 타임아웃 기준 시각과 호출 권한,
/// 정산 방향(공급자 지급인지 의뢰자 환불인지)이 확정되어야 추가할 수 있습니다.
/// deadline 은 처리 함수가 없는 동안 오해를 부르므로 필드와 이벤트 인자에서 함께 뺐습니다.
///
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
        uint64   deadline;     // 현재 상태의 제한 시간. 상태가 바뀔 때마다 새로 계산해 덮어쓴다
        address  provider;     // 작업을 수락한 제공자 (수락 전에는 address(0))
        uint256  price;        // 작업 대금 (결제 토큰 최소 단위)
        bytes32  resultHash;   // 결과물 검증을 위한 해시값
    }

    /// @notice 취소·환불·타임아웃이 없는 이 버전을 배포할 수 없는 체인
    uint256 private constant SSAFY_CHAIN_ID = 31221;

    /// @notice 정산에 사용될 ERC20 토큰 컨트랙트
    IERC20 public immutable paymentToken;

    /// @notice FUNDED 상태에서 공급자 수락까지 허용되는 시간(초)
    uint64 public immutable acceptDeadline;

    /// @notice ASSIGNED 상태에서 결과 제출까지 허용되는 시간(초)
    uint64 public immutable execDeadline;

    /// @notice SUBMITTED 상태에서 의뢰자 승인까지 허용되는 시간(초)
    uint64 public immutable approveDeadline;

    /// @notice 다음 작업에 부여될 고유 ID
    uint256 public nextJobId = 1;
    
    /// @notice Job ID와 작업 상세 정보 매핑
    mapping(uint256 => Job) public jobs;

    // ─────────────────────────────────────────────────────────────
    // 오류(Errors)
    // ─────────────────────────────────────────────────────────────

    /// @notice 취소·환불·타임아웃이 빠진 버전을 SSAFY 체인에 배포하려 할 때 되돌립니다.
    /// @dev 예치금 회수 경로가 approveResult 하나뿐이라 실자금이 잠길 수 있습니다.
    error SsafyDeploymentBlocked();

    /// @notice 제한 시간이 지난 뒤에 정상 경로 함수를 부를 때 되돌립니다.
    /// @dev 정각(block.timestamp == deadline)까지는 허용합니다.
    error DeadlineExpired();

    /// @notice 제한 시간이 지나기 전에 settleTimeout 을 부를 때 되돌립니다.
    /// @dev 정각에는 아직 정산할 수 없습니다. 정상 경로가 우선합니다.
    error DeadlineNotReached();

    // ─────────────────────────────────────────────────────────────
    // 이벤트(Events)
    // ─────────────────────────────────────────────────────────────

    /// @notice 새로운 작업이 생성되었을 때 발생
    event JobCreated(uint256 indexed jobId, address indexed client, uint256 price);
    /// @notice 작업 대금이 에스크로에 예치되었을 때 발생
    event JobFunded(uint256 indexed jobId, uint256 price, uint64 deadline);
    /// @notice 제공자가 작업을 수락했을 때 발생
    event JobAccepted(uint256 indexed jobId, address indexed provider, uint64 deadline);
    /// @notice 제공자가 작업 결과를 제출했을 때 발생
    event ResultSubmitted(uint256 indexed jobId, address indexed provider, bytes32 resultHash, uint64 deadline);
    /// @notice 작업이 완료되고 대금이 지급되었을 때 발생
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);
    /// @notice 작업이 취소되거나 환불되어 종료되었을 때 발생
    event JobCancelled(uint256 indexed jobId, address indexed client, uint256 refund);
    /// @notice 제한 시간 초과로 상태가 강제로 정리되었을 때 발생. 어느 상태에서 넘어왔는지 알린다
    event JobTimedOut(uint256 indexed jobId, JobState fromState);

    /// @notice 에스크로 컨트랙트를 초기화합니다.
    /// @dev SSAFY 체인에서는 배포 자체를 되돌립니다. 취소·환불·타임아웃 구현 후 이 검사를 제거합니다.
    /// @param paymentToken_ 에스크로에 사용할 결제 토큰 주소
    /// @param acceptDeadline_ 예치 후 수락까지의 기한(초)
    /// @param execDeadline_ 수락 후 제출까지의 기한(초)
    /// @param approveDeadline_ 제출 후 승인까지의 기한(초)
    constructor(
        address paymentToken_,
        uint64  acceptDeadline_,
        uint64  execDeadline_,
        uint64  approveDeadline_
    ) {
        if (block.chainid == SSAFY_CHAIN_ID) revert SsafyDeploymentBlocked();
        require(paymentToken_ != address(0), "Escrow: token is zero");
        require(
            acceptDeadline_ > 0 && execDeadline_ > 0 && approveDeadline_ > 0,
            "Escrow: deadline is zero"
        );
        paymentToken    = IERC20(paymentToken_);
        acceptDeadline  = acceptDeadline_;
        execDeadline    = execDeadline_;
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
        job.deadline = uint64(block.timestamp) + acceptDeadline;

        bool ok = paymentToken.transferFrom(msg.sender, address(this), job.price);
        require(ok, "Escrow: transferFrom failed");

        emit JobFunded(jobId, job.price, job.deadline);
    }

    /// @notice 아직 수락되지 않은 작업을 취소하고 예치금을 돌려받습니다.
    /// @dev FUNDED 에서만 동작합니다. CREATED 는 예치 전이라 잠길 자금이 없어 이번 범위 밖입니다.
    ///      수락 시한이 지난 뒤에도 호출할 수 있습니다. settleTimeout 과 결과가 같아 경쟁이 무해합니다.
    /// @param jobId 취소할 작업 ID
    function cancelJob(uint256 jobId) external {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.FUNDED, "Escrow: not FUNDED");

        uint256 refund = job.price;

        job.state = JobState.CANCELLED;

        bool ok = paymentToken.transfer(job.client, refund);
        require(ok, "Escrow: refund failed");

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
        if (block.timestamp > job.deadline) revert DeadlineExpired();

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
        if (block.timestamp > job.deadline) revert DeadlineExpired();

        job.resultHash = resultHash;
        job.state = JobState.SUBMITTED;
        job.deadline = uint64(block.timestamp) + approveDeadline;

        emit ResultSubmitted(jobId, msg.sender, resultHash, job.deadline);
    }

    // ─────────────────────────────────────────────────────────────
    // 공용(Anyone) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 제한 시간이 지난 작업을 정리합니다. 호출자 제한이 없습니다.
    /// @dev 호출자는 jobId 만 넘깁니다. 수령자·금액·정산 방향은 전부 컨트랙트가 상태로 결정합니다.
    ///      컨트랙트가 시한을 직접 검사하므로 누가 부르든 결과가 같습니다.
    ///
    ///      FUNDED    아무도 수락하지 않았다        -> 의뢰자 환불
    ///      ASSIGNED  공급자가 결과를 내지 않았다    -> 의뢰자 환불
    ///      SUBMITTED 의뢰자가 승인하지 않았다      -> 공급자 지급
    /// @param jobId 정리할 작업 ID
    function settleTimeout(uint256 jobId) external {
        Job storage job = jobs[jobId];
        JobState fromState = job.state;

        require(
            fromState == JobState.FUNDED
                || fromState == JobState.ASSIGNED
                || fromState == JobState.SUBMITTED,
            "Escrow: not settleable"
        );
        if (block.timestamp <= job.deadline) revert DeadlineNotReached();

        if (fromState == JobState.SUBMITTED) {
            job.state = JobState.COMPLETED;

            bool ok = paymentToken.transfer(job.provider, job.price);
            require(ok, "Escrow: payout failed");

            emit JobCompleted(jobId, job.provider, job.price);
        } else {
            job.state = JobState.CANCELLED;

            bool ok = paymentToken.transfer(job.client, job.price);
            require(ok, "Escrow: refund failed");

            emit JobCancelled(jobId, job.client, job.price);
        }

        emit JobTimedOut(jobId, fromState);
    }
}
