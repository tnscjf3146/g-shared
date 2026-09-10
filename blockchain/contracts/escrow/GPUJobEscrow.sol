// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title GPU 작업 에스크로 컨트랙트
/// @notice GPU 작업 대금을 예치했다가 결과 승인 또는 제한 시간 만료 시점에 정산합니다.
///
/// @dev 상태 전이와 자금 이동
/// ```
///   CREATED  --fundJob-->      FUNDED     대금이 잠기고 공급자가 지정된다
///   FUNDED   --acceptJob-->    ASSIGNED
///   ASSIGNED --submitResult--> SUBMITTED  결과 해시가 봉인된다
///   SUBMITTED --approveResult--> COMPLETED  공급자 지급
///
///   FUNDED   --cancelJob-->      CANCELLED  의뢰자 환불
///   FUNDED   --settleTimeout-->  CANCELLED  미수락 환불
///   ASSIGNED --settleTimeout-->  CANCELLED  미제출 환불
///   SUBMITTED --settleTimeout--> COMPLETED  미승인 지급
/// ```
/// 대기 상태 셋 모두에 제한 시간과 탈출 경로가 있습니다. 상대가 응답하지 않아도
/// 자금이 영구히 잠기지 않습니다.
///
/// @dev 제한 시간 경계
/// 정상 전이는 `block.timestamp <= deadline` 에서만, `settleTimeout` 은
/// `block.timestamp > deadline` 에서만 동작합니다. 같은 블록에 둘이 담겨도
/// 동시에 유효한 순간이 없으므로 트랜잭션 순서가 결과를 바꾸지 못합니다.
///
/// @dev MVP 신뢰 가정
/// `resultHash` 제출은 올바른 연산을 했다는 증명이 아닙니다. 잘못된 결과의 해시를
/// 올려도 의뢰자가 승인 기한 안에 대응하지 않으면 공급자가 지급받습니다.
/// 의뢰자가 기한 안에 결과를 확인한다는 운영 가정 위에 서 있으며, 결과 거절과
/// 분쟁·중재는 후속 작업입니다. 실제 가치가 걸리기 전에 반드시 추가해야 합니다.
///
/// @dev 공급자 선택
/// 의뢰자가 예치하면서 공급자를 직접 지정합니다. 지정된 주소만 수락할 수 있으므로
/// 선착순 경쟁이 없고, 가스가 0 인 체인에서도 제3자가 작업을 선점할 수 없습니다.
/// 화면에서는 Scheduler 가 추천한 후보 가운데 의뢰자가 고른 결과가 이 인자로 들어옵니다.
/// 추천 밖의 주소를 넣는 것은 막지 않습니다. 자기 자금으로 자기 상대를 고르는 일이라
/// 온체인이 관여할 근거가 없습니다.
///
/// @dev 이 방식으로 온체인이 보장하지 않는 것
/// 대금은 여전히 의뢰자가 `createJob` 에 적어 넣습니다. 공급자가 자동으로 수락하는
/// 운영 형태에서는 그 값을 걸러 줄 사람이 없으므로, 공급자 쪽 프로그램이 수락 전에
/// `jobs(jobId).price` 를 읽어 자기 단가와 대조해야 합니다. 온체인 강제가 아닙니다.
///
/// @dev 안전 장치
/// 자금이 나가는 함수는 CEI 순서를 지켜 상태를 먼저 바꾸고 토큰을 나중에 보냅니다.
/// 이것이 주 방어이고, `nonReentrant` 는 교차 함수 재진입까지 막는 이중 안전장치입니다.
/// 상태를 바꾸는 외부 함수 전부에 적용해, 훅을 실행하는 토큰이 들어와도 함수 사이를
/// 넘나드는 재진입이 성립하지 않습니다.
/// 토큰 호출은 `SafeERC20` 을 거칩니다.
///
/// @dev 온체인에는 정산 정보만 보관하며, workloadId·imageDigest·datasetHash 등
/// 작업 상세는 오프체인 DB 를 원본으로 삼습니다.
contract GPUJobEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice 에스크로 작업의 생명주기를 나타내는 상태 값
    enum JobState { NONE, CREATED, FUNDED, ASSIGNED, SUBMITTED, COMPLETED, CANCELLED }

    /// @notice 단일 작업(Job)에 대한 구조체
    /// @dev 변수 배치를 통해 단일 스토리지 슬롯(32바이트)에 맞춤: client(20) + state(1) + deadline(8) = 29 bytes.
    struct Job {
        address  client;       // 의뢰자 지갑 주소
        JobState state;        // 현재 작업 상태
        uint64   deadline;     // 현재 상태의 제한 시간. 상태가 바뀔 때마다 새로 계산해 덮어쓴다
        address  provider;     // 의뢰자가 예치할 때 지정한 공급자 (예치 전에는 address(0))
        uint256  price;        // 작업 대금 (결제 토큰 최소 단위)
        bytes32  resultHash;   // 결과물 검증을 위한 해시값
    }

    /// @notice 공급자 매칭 검증이 갖춰지기 전까지 배포를 막아 둔 체인
    uint256 private constant SSAFY_CHAIN_ID = 31221;

    /// @notice 정산에 사용될 ERC20 토큰 컨트랙트
    IERC20 public immutable paymentToken;

    /// @notice FUNDED 상태에서 공급자 수락까지 허용되는 시간 길이(초). 절대 시각이 아니다
    uint64 public immutable acceptTimeout;

    /// @notice ASSIGNED 상태에서 결과 제출까지 허용되는 시간 길이(초)
    uint64 public immutable executionTimeout;

    /// @notice SUBMITTED 상태에서 의뢰자 승인까지 허용되는 시간 길이(초)
    uint64 public immutable approvalTimeout;

    /// @notice 다음 작업에 부여될 고유 ID
    uint256 public nextJobId = 1;

    /// @notice Job ID와 작업 상세 정보 매핑
    mapping(uint256 => Job) public jobs;

    // ─────────────────────────────────────────────────────────────
    // 오류(Errors)
    // ─────────────────────────────────────────────────────────────

    /// @notice 아직 배포 승인을 받지 않은 버전을 SSAFY 체인에 올리려 할 때 되돌립니다.
    /// @dev 선착순 선점 문제는 의뢰자 지정으로 해소했습니다. 가드를 남겨 둔 이유는
    ///      공급자 선택 방식이 팀 확인을 기다리는 중이고, 대금이 온체인에서
    ///      강제되지 않아 공급자 쪽 대조가 전제로 남아 있기 때문입니다.
    ///      두 가지가 정리되면 이 검사를 제거합니다.
    error SsafyDeploymentBlocked();

    /// @notice 제한 시간이 지난 뒤에 정상 경로 함수를 부를 때 되돌립니다.
    /// @dev 정각(block.timestamp == deadline)까지는 허용합니다.
    error DeadlineExpired();

    /// @notice 제한 시간이 지나기 전에 settleTimeout 을 부를 때 되돌립니다.
    /// @dev 정각에는 아직 정산할 수 없습니다. 정상 경로가 우선합니다.
    error DeadlineNotReached();

    /// @notice 예치할 때 지정한 공급자 주소가 쓸 수 없는 값일 때 되돌립니다.
    /// @dev 영주소는 수락할 주체가 없어 자금이 시한까지 묶이고, 의뢰자 자신은
    ///      스스로 승인해 대금을 되가져가는 경로가 되므로 둘 다 막습니다.
    error InvalidProvider();

    /// @notice 지정되지 않은 주소가 수락하려 할 때 되돌립니다.
    error NotAssignedProvider();

    // ─────────────────────────────────────────────────────────────
    // 이벤트(Events)
    // ─────────────────────────────────────────────────────────────

    /// @notice 새로운 작업이 생성되었을 때 발생
    event JobCreated(uint256 indexed jobId, address indexed client, uint256 price);
    /// @notice 작업 대금이 에스크로에 예치되었을 때 발생. 지정된 공급자를 함께 알린다
    event JobFunded(uint256 indexed jobId, address indexed provider, uint256 price, uint64 deadline);
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
    /// @dev SSAFY 체인에서는 배포 자체를 되돌립니다. 배포 승인을 받은 뒤 이 검사를 제거합니다.
    /// @param paymentToken_ 에스크로에 사용할 결제 토큰 주소
    /// @param acceptTimeout_ 예치 후 수락까지의 기한(초)
    /// @param executionTimeout_ 수락 후 제출까지의 기한(초)
    /// @param approvalTimeout_ 제출 후 승인까지의 기한(초)
    constructor(
        address paymentToken_,
        uint64  acceptTimeout_,
        uint64  executionTimeout_,
        uint64  approvalTimeout_
    ) {
        if (block.chainid == SSAFY_CHAIN_ID) revert SsafyDeploymentBlocked();
        require(paymentToken_ != address(0), "Escrow: token is zero");
        require(
            acceptTimeout_ > 0 && executionTimeout_ > 0 && approvalTimeout_ > 0,
            "Escrow: deadline is zero"
        );
        paymentToken    = IERC20(paymentToken_);
        acceptTimeout  = acceptTimeout_;
        executionTimeout    = executionTimeout_;
        approvalTimeout = approvalTimeout_;
    }

    // ─────────────────────────────────────────────────────────────
    // 의뢰자(Client) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 새로운 작업을 생성합니다 (토큰 이동 없음).
    /// @param price 지급할 작업 대금
    /// @return jobId 생성된 작업의 고유 ID
    function createJob(uint256 price) external nonReentrant returns (uint256 jobId) {
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

    /// @notice 작업 대금을 예치하면서 작업을 맡을 공급자를 지정합니다.
    /// @dev 호출 전에 반드시 paymentToken.approve 로 컨트랙트가 대금을 출금할 수 있게 해야 합니다.
    ///      지정은 이 시점에만 하고 이후에는 바꿀 수 없습니다. 상대를 바꾸려면 취소하고 다시 만듭니다.
    /// @param jobId 대금을 예치할 작업 ID
    /// @param provider 이 작업을 맡을 공급자 주소. 이 주소만 acceptJob 을 부를 수 있습니다
    function fundJob(uint256 jobId, address provider) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.CREATED, "Escrow: not CREATED");
        if (provider == address(0) || provider == msg.sender) revert InvalidProvider();

        job.provider = provider;
        job.state = JobState.FUNDED;
        job.deadline = uint64(block.timestamp) + acceptTimeout;

        paymentToken.safeTransferFrom(msg.sender, address(this), job.price);

        emit JobFunded(jobId, provider, job.price, job.deadline);
    }

    /// @notice 아직 수락되지 않은 작업을 취소하고 예치금을 돌려받습니다.
    /// @dev FUNDED 에서만 동작합니다. CREATED 는 예치 전이라 잠길 자금이 없어 이번 범위 밖입니다.
    ///      수락 시한이 지난 뒤에도 호출할 수 있습니다. settleTimeout 과 결과가 같아 경쟁이 무해합니다.
    /// @param jobId 취소할 작업 ID
    function cancelJob(uint256 jobId) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.FUNDED, "Escrow: not FUNDED");

        uint256 refund = job.price;

        job.state = JobState.CANCELLED;

        paymentToken.safeTransfer(job.client, refund);

        emit JobCancelled(jobId, job.client, refund);
    }

    /// @notice 제출된 결과물을 승인하고 대금을 제공자에게 지급합니다.
    /// @param jobId 승인할 작업 ID
    function approveResult(uint256 jobId) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.client == msg.sender, "Escrow: not the client");
        require(job.state == JobState.SUBMITTED, "Escrow: not SUBMITTED");
        if (block.timestamp > job.deadline) revert DeadlineExpired();

        job.state = JobState.COMPLETED;

        paymentToken.safeTransfer(job.provider, job.price);

        emit JobCompleted(jobId, job.provider, job.price);
    }

    // ─────────────────────────────────────────────────────────────
    // 공급자(Provider) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 예치할 때 지정받은 작업을 수락합니다.
    /// @dev 지정된 주소만 부를 수 있습니다. 경쟁이 없으므로 먼저 부르는 것이 이득이 되지 않습니다.
    ///      수락하지 않고 두면 acceptTimeout 이 지난 뒤 의뢰자가 예치금을 되찾아 갑니다.
    /// @param jobId 수락할 작업 ID
    function acceptJob(uint256 jobId) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.state == JobState.FUNDED, "Escrow: not FUNDED");
        if (msg.sender != job.provider) revert NotAssignedProvider();
        if (block.timestamp > job.deadline) revert DeadlineExpired();

        job.state = JobState.ASSIGNED;
        job.deadline = uint64(block.timestamp) + executionTimeout;

        emit JobAccepted(jobId, msg.sender, job.deadline);
    }

    /// @notice 작업이 완료되었음을 알리고 결과물에 대한 해시를 등록합니다.
    /// @dev 백엔드가 서명하지 않고 공급자가 직접 트랜잭션을 발생시켜 부인 방지(Non-repudiation)를 달성합니다.
    /// @param jobId 결과물을 제출할 작업 ID
    /// @param resultHash 결과물에 대한 해시값
    function submitResult(uint256 jobId, bytes32 resultHash) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.provider == msg.sender, "Escrow: not the provider");
        require(job.state == JobState.ASSIGNED, "Escrow: not ASSIGNED");
        require(resultHash != bytes32(0), "Escrow: empty result hash");
        if (block.timestamp > job.deadline) revert DeadlineExpired();

        job.resultHash = resultHash;
        job.state = JobState.SUBMITTED;
        job.deadline = uint64(block.timestamp) + approvalTimeout;

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
    function settleTimeout(uint256 jobId) external nonReentrant {
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

            paymentToken.safeTransfer(job.provider, job.price);

            emit JobCompleted(jobId, job.provider, job.price);
        } else {
            job.state = JobState.CANCELLED;

            paymentToken.safeTransfer(job.client, job.price);

            emit JobCancelled(jobId, job.client, job.price);
        }

        emit JobTimedOut(jobId, fromState);
    }
}
