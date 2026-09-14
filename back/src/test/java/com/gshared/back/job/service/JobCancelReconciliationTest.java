package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.global.blockchain.EscrowVerification.Verdict;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.worker.websocket.WorkerMessageSender;
import com.gshared.back.workload.domain.BaseModel;
import com.gshared.back.workload.repository.BaseModelRepository;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.when;

/**
 * 취소와 종료가 겹쳤을 때, 체인이 말하는 쪽으로 맞춘다.
 *
 * <p>사용자가 취소를 누르는 순간 워커는 이미 결과를 올리고 {@code submitResult} 까지
 * 끝냈을 수 있다. 그러면 <b>돈은 지불됐는데 기록은 취소 중</b>인 Job 이 남는다. 반대로
 * 워커가 {@code abortJob} 으로 환불해 놓고 죽었을 수도 있다. 둘 다 Backend 혼자서는
 * 구분할 수 없다 -- 워커의 말만 듣고 정하면, 어느 쪽으로 틀려도 남의 돈이 잘못된
 * 자리에 간다.
 *
 * <p>그래서 여기서 확인하는 것은 한 문장이다: <b>종료 상태는 체인이 정한다.</b>
 * 체인이 아니라고 말할 때만 막고, 모를 때는 막지 않는다 -- RPC 장애를 근거로
 * CANCELLING 에 묶어 두면 워커는 이미 슬롯을 놓았으므로 그 Job 은 아무도 끝낼 수
 * 없게 된다.
 *
 * <p>{@link EscrowVerification} 은 여기서 대역이다. 진짜 노드에 붙는 쪽은
 * {@code EscrowChainClientLiveTest} 가 배포된 컨트랙트로 따로 확인한다 -- 여기서
 * 보려는 것은 판정을 받은 뒤 서비스가 무엇을 하느냐이지, 판정을 어떻게 읽느냐가
 * 아니다.
 */
@SpringBootTest
@Transactional
class JobCancelReconciliationTest {

    private static final String OPERATOR = "0x" + "c".repeat(40);
    private static final String SHA256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
    private static final String ESCROW_JOB_ID = "17";

    @Autowired
    private JobService jobService;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private EntityManager em;

    @MockitoBean
    private WorkerMessageSender messageSender;
    @MockitoBean
    private EscrowVerification escrow;          // 체인이 무엇을 말하는지 여기서 정한다

    private User client;
    private Worker worker;
    private Job job;

    @BeforeEach
    void setUp() {
        client = userRepository.save(User.createUser(
                "client-" + UUID.randomUUID() + "@gshared.io", "encoded", "의뢰자"));
        client.linkWallet("0x" + "a".repeat(40));
        User provider = userRepository.save(User.createUser(
                "provider-" + UUID.randomUUID() + "@gshared.io", "encoded", "공급자"));
        provider.linkWallet("0x" + "b".repeat(40));
        worker = workerRepository.save(Worker.register(
                "worker-recon-" + UUID.randomUUID(), provider, "hash-" + UUID.randomUUID(),
                OPERATOR, "0.1.0", "Windows 11", true, "RTX 4090", 24576, 42000.0,
                "transformer-bench-v1"));
        worker.goOnline(LocalDateTime.now());

        BaseModel baseModel = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow();
        Dataset dataset = datasetRepository.save(Dataset.create(
                client, 52_428_800L, 10_000, 2_500_000L, 1_800_000L, 250.0, 1024, null, null));
        job = jobRepository.save(Job.create(client, baseModel.getWorkload().getId(), baseModel.getId(),
                dataset.getId(), "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1",
                worker.getId(), 9L));

        em.flush();
        jobRepository.fundIfCreated(job.getId(), 31221L, null, ESCROW_JOB_ID, null);
        jobRepository.assignIfFunded(job.getId(), worker.getId());
        jobRepository.startIfAssigned(job.getId(), worker.getId(), "0xaccept", LocalDateTime.now());
        em.clear();

        workerRepository.findById(worker.getId()).orElseThrow().startJob(job.getId());
        em.flush();

        when(messageSender.send(any(), any())).thenReturn(true);
        // 기본값: 아무것도 확인되지 않은 상태. 각 테스트가 필요한 것만 바꾼다.
        when(escrow.acceptedOnChain(any(), any(), any())).thenReturn(Verdict.UNKNOWN);
        when(escrow.resultOnChain(any(), any(), any(), any())).thenReturn(Verdict.UNKNOWN);
        when(escrow.cancelledOnChain(any(), any())).thenReturn(Verdict.UNKNOWN);
    }

    // ── 준비 ────────────────────────────────────────────────────

    /** §18 업로드 검증까지 끝난 상태로 만든다 — 이 기록이 없으면 JOB_RESULT 는 애초에 거절된다. */
    private void recordVerifiedUpload() {
        jobRepository.findById(job.getId()).orElseThrow()
                .recordResultUpload(objectKey(), 100L, SHA256);
        em.flush();
    }

    /** 사용자가 취소를 눌러 CANCELLING 으로 만든다. */
    private void userCancels() {
        jobRepository.markCancellingIfActive(job.getId(), client.getId());
        em.clear();
    }

    private String objectKey() {
        return "jobs/" + job.getId() + "/result.tar";
    }

    private Job reload() {
        em.flush();
        em.clear();
        return jobRepository.findById(job.getId()).orElseThrow();
    }

    private WorkerStatus workerStatus() {
        em.flush();
        em.clear();
        return workerRepository.findById(worker.getId()).orElseThrow().getStatus();
    }

    // ── 취소가 결과보다 늦었다: 체인 COMPLETED ──────────────────

    @Test
    @DisplayName("CANCELLING + 체인 COMPLETED + JOB_RESULT → 거짓 CANCELLED 가 아니라 COMPLETED 로 맞춘다")
    void cancellingButChainCompleted() {
        recordVerifiedUpload();
        userCancels();
        when(escrow.resultOnChain(isNull(), eq(ESCROW_JOB_ID), eq(OPERATOR), eq("0x" + SHA256)))
                .thenReturn(Verdict.CONFIRMED);

        boolean completed = jobService.completeJob(worker.getId(), job.getId(),
                objectKey(), "0x" + SHA256, "0xsubmit", 982.4);

        assertThat(completed).isTrue();
        Job done = reload();
        assertThat(done.getStatus()).isEqualTo(JobStatus.COMPLETED);
        assertThat(done.getSubmitTxHash()).isEqualTo("0xsubmit");
        assertThat(done.getActualRuntimeSec()).isEqualTo(982.4);
        // 취소를 눌렀다는 사실이 COMPLETED 안에 남아야 한다. 상태만으로는 알 수 없다.
        assertThat(done.getReconciliationReason())
                .contains("취소").contains("COMPLETED");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);
    }

    @Test
    @DisplayName("체인이 답하지 않아도(UNKNOWN) 검증된 JOB_RESULT 는 COMPLETED 로 맞춘다 — 노드 장애로 Job 을 묶지 않는다")
    void cancellingWithUnknownChainStillCompletes() {
        recordVerifiedUpload();
        userCancels();
        // escrow 기본값이 UNKNOWN 이다.

        assertThat(jobService.completeJob(worker.getId(), job.getId(),
                objectKey(), "0x" + SHA256, "0xsubmit", 982.4)).isTrue();
        assertThat(reload().getStatus()).isEqualTo(JobStatus.COMPLETED);
    }

    @Test
    @DisplayName("체인이 COMPLETED 가 아니라고 하면 CANCELLING 에 남는다 — 워커 말만 믿고 완료로 확정하지 않는다")
    void cancellingWithContradictedResultStaysCancelling() {
        recordVerifiedUpload();
        userCancels();
        when(escrow.resultOnChain(any(), any(), any(), any())).thenReturn(Verdict.CONTRADICTED);

        assertThat(jobService.completeJob(worker.getId(), job.getId(),
                objectKey(), "0x" + SHA256, "0xsubmit", 982.4)).isFalse();

        Job still = reload();
        assertThat(still.getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(still.getReconciliationReason()).contains("on-chain mismatch");
        assertThat(workerStatus()).as("놓아주지 않는다").isEqualTo(WorkerStatus.RUNNING);
    }

    // ── 취소가 이겼다: 체인 CANCELLED ───────────────────────────

    @Test
    @DisplayName("CANCELLING + 체인 CANCELLED + JOB_CANCELLED → CANCELLED 로 확정")
    void cancellingAndChainCancelled() {
        userCancels();
        when(escrow.cancelledOnChain(isNull(), eq(ESCROW_JOB_ID))).thenReturn(Verdict.CONFIRMED);

        assertThat(jobService.confirmCancelled(worker.getId(), job.getId(), "0xabort", 512.3)).isTrue();

        Job cancelled = reload();
        assertThat(cancelled.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(cancelled.getAbortTxHash()).isEqualTo("0xabort");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);
    }

    @Test
    @DisplayName("CANCELLING 중 JOB_FAILED + 체인 CANCELLED → FAILED 가 아니라 CANCELLED (사유는 남는다)")
    void failureDuringCancellingBecomesCancelled() {
        userCancels();
        when(escrow.cancelledOnChain(isNull(), eq(ESCROW_JOB_ID))).thenReturn(Verdict.CONFIRMED);

        assertThat(jobService.failJob(worker.getId(), job.getId(),
                "CUDA_OOM", "CUDA out of memory.", "0xabort", 301.5)).isTrue();

        Job cancelled = reload();
        assertThat(cancelled.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(cancelled.getFailReason()).startsWith("CUDA_OOM");
        assertThat(cancelled.getReconciliationReason()).contains("CANCELLED");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);
    }

    // ── 체인이 아니라고 말한다: 확정하지 않는다 ─────────────────

    @Test
    @DisplayName("체인이 여전히 ASSIGNED 면 JOB_CANCELLED 를 받아도 CANCELLED 로 확정하지 않는다")
    void chainStillAssignedBlocksFalseCancel() {
        userCancels();
        when(escrow.cancelledOnChain(isNull(), eq(ESCROW_JOB_ID))).thenReturn(Verdict.CONTRADICTED);

        assertThat(jobService.confirmCancelled(worker.getId(), job.getId(), "0xabort", 512.3)).isFalse();

        Job still = reload();
        assertThat(still.getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(still.getAbortTxHash()).as("환불되지 않았으므로 흔적도 남기지 않는다").isNull();
        assertThat(still.getReconciliationReason()).contains("not CANCELLED");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.RUNNING);
    }

    @Test
    @DisplayName("체인이 ASSIGNED 면 abortTxHash 를 단 JOB_FAILED 도 받지 않는다 — 예치금이 묶인 채로 종료되지 않게")
    void chainStillAssignedBlocksFalseFailure() {
        userCancels();
        when(escrow.cancelledOnChain(isNull(), eq(ESCROW_JOB_ID))).thenReturn(Verdict.CONTRADICTED);

        assertThat(jobService.failJob(worker.getId(), job.getId(),
                "CUDA_OOM", "CUDA out of memory.", "0xabort", 301.5)).isFalse();

        assertThat(reload().getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(workerStatus()).isEqualTo(WorkerStatus.RUNNING);
    }

    @Test
    @DisplayName("RPC 가 답하지 않으면(UNKNOWN) 취소 확정을 막지 않는다 — 모르는 것은 틀린 것이 아니다")
    void unknownChainDoesNotBlockCancel() {
        userCancels();
        // 기본값 UNKNOWN.

        assertThat(jobService.confirmCancelled(worker.getId(), job.getId(), "0xabort", 512.3)).isTrue();
        assertThat(reload().getStatus()).isEqualTo(JobStatus.CANCELLED);
    }

    @Test
    @DisplayName("abortTxHash 가 없는 JOB_FAILED 는 체인에 묻지 않는다 — 환불을 주장하지 않았다")
    void failureWithoutAbortIsNotChecked() {
        when(escrow.cancelledOnChain(any(), any())).thenReturn(Verdict.CONTRADICTED);

        assertThat(jobService.failJob(worker.getId(), job.getId(),
                "RUNTIME_ERROR", "학습이 죽었다", null, 12.0)).isTrue();

        assertThat(reload().getStatus()).isEqualTo(JobStatus.FAILED);
    }

    // ── 겹치지 않은 정상 경로 ───────────────────────────────────

    @Test
    @DisplayName("취소가 없었던 RUNNING → COMPLETED 정상 경로는 그대로다 — 대조 사유도 남지 않는다")
    void plainRunningToCompletedUnchanged() {
        recordVerifiedUpload();
        when(escrow.resultOnChain(isNull(), eq(ESCROW_JOB_ID), eq(OPERATOR), eq("0x" + SHA256)))
                .thenReturn(Verdict.CONFIRMED);

        assertThat(jobService.completeJob(worker.getId(), job.getId(),
                objectKey(), "0x" + SHA256, "0xsubmit", 982.4)).isTrue();

        Job done = reload();
        assertThat(done.getStatus()).isEqualTo(JobStatus.COMPLETED);
        assertThat(done.getReconciliationReason()).isNull();
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);
    }

    @Test
    @DisplayName("RUNNING 중 JOB_FAILED 정상 경로도 그대로 FAILED 다")
    void plainRunningToFailedUnchanged() {
        when(escrow.cancelledOnChain(isNull(), eq(ESCROW_JOB_ID))).thenReturn(Verdict.CONFIRMED);

        assertThat(jobService.failJob(worker.getId(), job.getId(),
                "CUDA_OOM", "CUDA out of memory.", "0xabort", 301.5)).isTrue();

        Job failed = reload();
        assertThat(failed.getStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(failed.getFailReason()).startsWith("CUDA_OOM");
    }
}
