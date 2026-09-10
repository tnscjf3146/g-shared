// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title GPU 작업 에스크로 컨트랙트
/// @notice GPU 작업 대금을 예치했다가 결과 제출 또는 제한 시간 만료 시점에 정산합니다.
///
/// @dev 상태 전이와 자금 이동
/// ```
///   (없음)   --createAndFundJob--> FUNDED     대금이 잠기고 공급자가 지정된다
///   FUNDED   --acceptJob-->        ASSIGNED
///   ASSIGNED --submitResult-->     COMPLETED  결과 해시가 봉인되고 즉시 지급된다
///
///   FUNDED   --cancelJob-->        CANCELLED  의뢰자 취소
///   FUNDED   --settleTimeout-->    CANCELLED  미수락 환불
///   ASSIGNED --abortJob-->         CANCELLED  공급자 반납
///   ASSIGNED --settleTimeout-->    CANCELLED  미제출 환불
/// ```
/// 지급 경로는 `submitResult` 하나이고 환불 경로는 셋입니다. 대기 상태 둘 모두에
/// 제한 시간과 탈출 경로가 있어, 상대가 응답하지 않아도 자금이 영구히 잠기지 않습니다.
///
/// @dev 승인 단계가 없는 이유
/// 이전 버전에는 `SUBMITTED` 상태와 승인 기한이 있었으나, 결과를 거절하는 함수가 없어
/// 의뢰자가 무엇을 하든 정산 결과가 같았습니다. 판단할 수단이 없는데 대기 시간만 두는
/// 것은 실제로 보호가 되지 않으므로 제거했습니다. 이의 제기와 중재를 넣을 때 다시
/// 만듭니다.
///
/// @dev 제한 시간 경계
/// 정상 전이는 `block.timestamp <= deadline` 에서만, `settleTimeout` 은
/// `block.timestamp > deadline` 에서만 동작합니다. 같은 블록에 둘이 담겨도
/// 동시에 유효한 순간이 없으므로 트랜잭션 순서가 결과를 바꾸지 못합니다.
///
/// @dev 공급자 선택
/// 의뢰자가 예치하면서 공급자를 직접 지정합니다. 지정된 주소만 수락할 수 있으므로
/// 선착순 경쟁이 없고, 가스가 0 인 체인에서도 제3자가 작업을 선점할 수 없습니다.
/// 화면에서는 Scheduler 가 추천한 후보 가운데 의뢰자가 고른 결과가 이 인자로 들어옵니다.
/// 추천 밖의 주소를 넣는 것은 막지 않습니다. 자기 자금으로 자기 상대를 고르는 일이라
/// 온체인이 관여할 근거가 없습니다.
///
/// @dev 온체인이 보장하지 않는 것
/// `resultHash` 제출은 올바른 연산을 했다는 증명이 아닙니다. 잘못된 결과의 해시를
/// 올려도 온체인에서 되돌릴 수 없고, 제출과 동시에 정산이 최종 완료됩니다.
/// 대금도 의뢰자가 트랜잭션에 적어 보내므로 온체인에서 강제되지 않습니다. 공급자 쪽
/// 프로그램이 수락 전에 백엔드 배정 정보와 `jobs(jobId)` 를 대조하는 것이 유일한
/// 방어선입니다. 분쟁 기능이 추가되기 전에는 실제 가치가 큰 결제에 쓰지 않습니다.
///
/// @dev 안전 장치
/// 자금이 나가는 함수는 CEI 순서를 지켜 상태를 먼저 바꾸고 토큰을 나중에 보냅니다.
/// 이것이 주 방어이고, `nonReentrant` 는 교차 함수 재진입까지 막는 이중 안전장치입니다.
/// 상태를 바꾸는 외부 함수 전부에 적용해, 훅을 실행하는 토큰이 들어와도 함수 사이를
/// 넘나드는 재진입이 성립하지 않습니다.
/// 토큰 호출은 `SafeERC20` 을 거칩니다.
///
/// @dev 온체인에는 정산 정보만 보관하며, workloadId·imageDigest·datasetHash 등
/// 작업 상세는 오프체인 DB 를 원본으로 삼습니다. `externalRef` 는 그 DB 작업을
/// 가리키는 조회용 참조값이며 인증 수단이 아닙니다.
contract GPUJobEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    /// @notice 에스크로 작업의 생명주기를 나타내는 상태 값
    enum JobState { NONE, FUNDED, ASSIGNED, COMPLETED, CANCELLED }

    /// @notice 단일 작업(Job)에 대한 구조체
    /// @dev 변수 배치를 통해 첫 슬롯에 맞춤: client(20) + state(1) + deadline(8) = 29 bytes.
    struct Job {
        address  client;       // 의뢰자 지갑 주소
        JobState state;        // 현재 작업 상태
        uint64   deadline;     // 현재 상태의 제한 시간. 상태가 바뀔 때마다 새로 계산해 덮어쓴다
        address  provider;     // 의뢰자가 예치할 때 지정한 공급자의 서명 주소
        uint256  price;        // 작업 대금 (결제 토큰 최소 단위)
        bytes32  resultHash;   // 결과물 검증을 위한 해시값
    }

    /// @notice 배포 승인을 받지 않은 버전을 막아 둔 체인
    uint256 private constant SSAFY_CHAIN_ID = 31221;

    /// @notice 정산에 사용될 ERC20 토큰 컨트랙트
    IERC20 public immutable paymentToken;

    /// @notice FUNDED 상태에서 공급자 수락까지 허용되는 시간 길이(초). 절대 시각이 아니다
    uint64 public immutable acceptTimeout;

    /// @notice ASSIGNED 상태에서 결과 제출까지 허용되는 시간 길이(초)
    /// @dev 작업을 강제로 멈추는 값이 아니다. 공급자 쪽이 응답 불능이 되었을 때
    ///      의뢰자가 예치금을 되찾을 수 있는 시점을 정한다. 짧으면 정상 작업이
    ///      완료 직전에 환불될 수 있어 넉넉하게 잡는다.
    uint64 public immutable executionTimeout;

    /// @notice 다음 작업에 부여될 고유 ID
    uint256 public nextJobId = 1;

    /// @notice Job ID와 작업 상세 정보 매핑
    mapping(uint256 => Job) public jobs;

    // ─────────────────────────────────────────────────────────────
    // 오류(Errors)
    // ─────────────────────────────────────────────────────────────

    /// @notice 배포 승인을 받지 않은 버전을 SSAFY 체인에 올리려 할 때 되돌립니다.
    /// @dev 선착순 선점 문제는 의뢰자 지정으로 해소했습니다. 가드를 남겨 둔 이유는
    ///      대금이 온체인에서 강제되지 않아 공급자 쪽 대조가 전제로 남아 있고,
    ///      연동 측 준비가 끝나지 않았기 때문입니다.
    error SsafyDeploymentBlocked();

    /// @notice 제한 시간이 지난 뒤에 정상 경로 함수를 부를 때 되돌립니다.
    /// @dev 정각(block.timestamp == deadline)까지는 허용합니다.
    error DeadlineExpired();

    /// @notice 제한 시간이 지나기 전에 settleTimeout 을 부를 때 되돌립니다.
    /// @dev 정각에는 아직 정산할 수 없습니다. 정상 경로가 우선합니다.
    error DeadlineNotReached();

    /// @notice 예치할 때 지정한 공급자 주소가 쓸 수 없는 값일 때 되돌립니다.
    /// @dev 영주소는 수락할 주체가 없어 자금이 시한까지 묶이고, 의뢰자 자신은
    ///      스스로 수락하고 제출해 대금을 되가져가는 경로가 되므로 둘 다 막습니다.
    error InvalidProvider();

    /// @notice 지정되지 않은 주소가 공급자 함수를 부를 때 되돌립니다.
    error NotAssignedProvider();

    // ─────────────────────────────────────────────────────────────
    // 이벤트(Events)
    // ─────────────────────────────────────────────────────────────

    /// @notice 작업이 생성되고 대금이 예치되었을 때 발생. 여기서부터 실제 일감이다
    /// @dev externalRef 로 오프체인 작업을 찾고, provider 로 공급자 노드가 자기 작업만 고른다
    event JobFunded(
        uint256 indexed jobId,
        bytes32 indexed externalRef,
        address indexed provider,
        address client,
        uint256 price,
        uint64  deadline
    );
    /// @notice 지정된 공급자가 작업을 수락했을 때 발생
    event JobAccepted(uint256 indexed jobId, address indexed provider, uint64 deadline);
    /// @notice 공급자가 작업 결과를 제출했을 때 발생. JobCompleted 와 같은 트랜잭션에 실린다
    event ResultSubmitted(uint256 indexed jobId, address indexed provider, bytes32 resultHash);
    /// @notice 작업이 완료되고 대금이 지급되었을 때 발생
    event JobCompleted(uint256 indexed jobId, address indexed provider, uint256 payout);
    /// @notice 작업이 취소되거나 환불되어 종료되었을 때 발생. 환불 셋 모두가 이 이벤트를 낸다
    event JobCancelled(uint256 indexed jobId, address indexed client, uint256 refund);
    /// @notice 공급자가 작업을 반납했을 때 발생. JobCancelled 와 함께 실려 사유를 구분한다
    event JobAborted(uint256 indexed jobId, address indexed provider);
    /// @notice 제한 시간 초과로 정리되었을 때 발생. JobCancelled 와 함께 실려 사유를 구분한다
    event JobTimedOut(uint256 indexed jobId, JobState fromState);

    /// @notice 에스크로 컨트랙트를 초기화합니다.
    /// @dev SSAFY 체인에서는 배포 자체를 되돌립니다. 배포 승인을 받은 뒤 이 검사를 제거합니다.
    /// @param paymentToken_ 에스크로에 사용할 결제 토큰 주소
    /// @param acceptTimeout_ 예치 후 수락까지의 기한(초)
    /// @param executionTimeout_ 수락 후 제출까지의 기한(초)
    constructor(address paymentToken_, uint64 acceptTimeout_, uint64 executionTimeout_) {
        if (block.chainid == SSAFY_CHAIN_ID) revert SsafyDeploymentBlocked();
        require(paymentToken_ != address(0), "Escrow: token is zero");
        require(acceptTimeout_ > 0 && executionTimeout_ > 0, "Escrow: deadline is zero");
        paymentToken     = IERC20(paymentToken_);
        acceptTimeout    = acceptTimeout_;
        executionTimeout = executionTimeout_;
    }

    // ─────────────────────────────────────────────────────────────
    // 의뢰자(Client) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 작업을 만들고 대금을 예치하면서 작업을 맡을 공급자를 지정합니다.
    /// @dev 호출 전에 반드시 paymentToken.approve 로 컨트랙트가 대금을 출금할 수 있게 해야 합니다.
    ///      생성과 예치를 한 트랜잭션에 묶어, 대금 없는 작업이 스토리지에 남지 않습니다.
    ///      토큰 전송이 실패하면 작업 생성과 nextJobId 증가까지 함께 되돌아갑니다.
    /// @param externalRef 오프체인 작업을 가리키는 참조값. 백엔드가 발급하며 인증 수단이 아니다
    /// @param price 지급할 작업 대금 (결제 토큰 최소 단위)
    /// @param provider 이 작업을 맡을 공급자의 서명 주소. 이 주소만 acceptJob 을 부를 수 있습니다
    /// @return jobId 생성된 작업의 고유 ID
    function createAndFundJob(bytes32 externalRef, uint256 price, address provider)
        external
        nonReentrant
        returns (uint256 jobId)
    {
        require(price > 0, "Escrow: price is zero");
        if (provider == address(0) || provider == msg.sender) revert InvalidProvider();

        jobId = nextJobId++;
        uint64 deadline = uint64(block.timestamp) + acceptTimeout;

        jobs[jobId] = Job({
            client:     msg.sender,
            state:      JobState.FUNDED,
            deadline:   deadline,
            provider:   provider,
            price:      price,
            resultHash: bytes32(0)
        });

        paymentToken.safeTransferFrom(msg.sender, address(this), price);

        emit JobFunded(jobId, externalRef, provider, msg.sender, price, deadline);
    }

    /// @notice 아직 수락되지 않은 작업을 취소하고 예치금을 돌려받습니다.
    /// @dev 수락 시한이 지난 뒤에도 호출할 수 있습니다. settleTimeout 과 결과가 같아 경쟁이 무해합니다.
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

    /// @notice 결과물 해시를 등록하고 대금을 받습니다. 제출과 정산이 한 트랜잭션에서 끝납니다.
    /// @dev 백엔드가 서명하지 않고 공급자가 직접 트랜잭션을 발생시켜 부인 방지를 달성합니다.
    ///      의뢰자가 승인하거나 거절하는 단계는 없습니다. 되돌릴 수 없으므로 노드가
    ///      수락 전에 백엔드 배정 정보와 온체인 작업을 대조해야 합니다.
    /// @param jobId 결과물을 제출할 작업 ID
    /// @param resultHash 결과물 파일의 SHA-256 해시
    function submitResult(uint256 jobId, bytes32 resultHash) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.state == JobState.ASSIGNED, "Escrow: not ASSIGNED");
        if (msg.sender != job.provider) revert NotAssignedProvider();
        require(resultHash != bytes32(0), "Escrow: empty result hash");
        if (block.timestamp > job.deadline) revert DeadlineExpired();

        uint256 payout = job.price;

        job.resultHash = resultHash;
        job.state = JobState.COMPLETED;

        paymentToken.safeTransfer(job.provider, payout);

        emit ResultSubmitted(jobId, msg.sender, resultHash);
        emit JobCompleted(jobId, job.provider, payout);
    }

    /// @notice 작업을 수행할 수 없어 반납합니다. 예치금은 의뢰자에게 돌아갑니다.
    /// @dev 학습 실패처럼 프로그램은 살아 있으나 계속할 수 없는 경우를 위한 경로입니다.
    ///      시한 검사를 두지 않습니다. 기한이 지난 뒤에는 settleTimeout 과 결과가 같아
    ///      먼저 실행된 트랜잭션만 성공하고 나머지는 상태 검사에서 안전하게 실패합니다.
    ///      실패 사유 원문은 온체인에 남기지 않습니다. 반납했다는 사실만 기록합니다.
    /// @param jobId 반납할 작업 ID
    function abortJob(uint256 jobId) external nonReentrant {
        Job storage job = jobs[jobId];

        require(job.state == JobState.ASSIGNED, "Escrow: not ASSIGNED");
        if (msg.sender != job.provider) revert NotAssignedProvider();

        uint256 refund = job.price;

        job.state = JobState.CANCELLED;

        paymentToken.safeTransfer(job.client, refund);

        emit JobCancelled(jobId, job.client, refund);
        emit JobAborted(jobId, msg.sender);
    }

    // ─────────────────────────────────────────────────────────────
    // 공용(Anyone) 기능
    // ─────────────────────────────────────────────────────────────

    /// @notice 제한 시간이 지난 작업을 정리합니다. 호출자 제한이 없습니다.
    /// @dev 호출자는 jobId 만 넘깁니다. 수령자와 금액은 전부 컨트랙트가 상태로 결정합니다.
    ///      지급 경로가 submitResult 하나로 모였으므로 이 함수는 환불 전용입니다.
    ///
    ///      FUNDED    아무도 수락하지 않았다     -> 의뢰자 환불
    ///      ASSIGNED  공급자가 결과를 내지 않았다 -> 의뢰자 환불
    /// @param jobId 정리할 작업 ID
    function settleTimeout(uint256 jobId) external nonReentrant {
        Job storage job = jobs[jobId];
        JobState fromState = job.state;

        require(
            fromState == JobState.FUNDED || fromState == JobState.ASSIGNED,
            "Escrow: not settleable"
        );
        if (block.timestamp <= job.deadline) revert DeadlineNotReached();

        uint256 refund = job.price;

        job.state = JobState.CANCELLED;

        paymentToken.safeTransfer(job.client, refund);

        emit JobCancelled(jobId, job.client, refund);
        emit JobTimedOut(jobId, fromState);
    }

}
