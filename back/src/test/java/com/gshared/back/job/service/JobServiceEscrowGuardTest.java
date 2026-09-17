package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.worker.websocket.WorkerMessageSender;
import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import com.gshared.back.workload.domain.BaseModel;
import com.gshared.back.workload.repository.BaseModelRepository;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Escrow 회신 반영 — 워커 보고값을 Backend 기록과 대조하는 fail-closed 규칙.
 * <ul>
 *   <li>JOB_ACCEPTED: escrowJobId·operatorAddress 가 다르면 RUNNING 대신 CANCELLING + JOB_CANCEL + 대조 사유
 *   <li>JOB_RESULT: resultHash 가 §18 검증 sha256(0x) 과 다르면 COMPLETED 하지 않음 + 대조 사유
 *   <li>JOB_REJECTED: RPC 장애만 재매칭, 나머지는 FAILED + 대조 사유
 *   <li>취소: ASSIGNED/RUNNING 은 CANCELLING 을 거쳐 JOB_CANCELLED 로 확정
 * </ul>
 */
@SpringBootTest
@Transactional
class JobServiceEscrowGuardTest {

    private static final String OPERATOR = "0x" + "c".repeat(40);
    private static final String SHA256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

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
    private WorkerMessageSender messageSender;   // JOB_CANCEL 발신 여부만 본다

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
                "worker-guard-" + UUID.randomUUID(), provider, "hash-" + UUID.randomUUID(), OPERATOR, "0.1.0",
                "Windows 11", true, "RTX 4090", 24576, 42000.0, "transformer-bench-v1"));
        worker.goOnline(LocalDateTime.now());

        BaseModel baseModel = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow();
        Dataset dataset = datasetRepository.save(Dataset.create(
                client, 52_428_800L, 10_000, 2_500_000L, 1_800_000L, 250.0, 1024, null, null));
        job = jobRepository.save(Job.create(client, baseModel.getWorkload().getId(), baseModel.getId(),
                dataset.getId(), "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", worker.getId(), 9L));

        em.flush();   // 조건부 UPDATE(clearAutomatically) 전에 대기 중인 변경을 DB 로
        jobRepository.fundIfCreated(job.getId(), 31221L, null, "17", null);
        jobRepository.assignIfFunded(job.getId(), worker.getId());
        em.clear();
        when(messageSender.send(any(), any())).thenReturn(true);
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

    private void makeRunning() {
        jobRepository.startIfAssigned(job.getId(), worker.getId(), "0xaccept", LocalDateTime.now());
        em.clear();
    }

    // ── JOB_ACCEPTED ────────────────────────────────────────────

    @Test
    @DisplayName("escrowJobId·operatorAddress 가 기록과 같으면 RUNNING + acceptTxHash, 워커도 RUNNING")
    void accept_when_matches() {
        boolean started = jobService.acceptJob(worker.getId(), job.getId(), "17", OPERATOR.toUpperCase(), "0xaccept");

        assertThat(started).isTrue();
        Job running = reload();
        assertThat(running.getStatus()).isEqualTo(JobStatus.RUNNING);
        assertThat(running.getAcceptTxHash()).isEqualTo("0xaccept");
        assertThat(running.getReconciliationReason()).isNull();
        assertThat(workerStatus()).isEqualTo(WorkerStatus.RUNNING);
        verify(messageSender, never()).send(any(), any());
    }

    @Test
    @DisplayName("escrowJobId 가 다르면 fail closed — RUNNING 대신 CANCELLING, JOB_CANCEL 발신, 대조 사유 기록")
    void accept_with_wrong_escrow_job_id() {
        boolean started = jobService.acceptJob(worker.getId(), job.getId(), "99", OPERATOR, "0xaccept");

        assertThat(started).isFalse();
        Job stopped = reload();
        assertThat(stopped.getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(stopped.getAcceptTxHash()).isNull();
        assertThat(stopped.getReconciliationReason()).contains("escrowJobId mismatch").contains("99");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);   // RUNNING 으로 올리지 않는다

        ArgumentCaptor<WorkerMessage> sent = ArgumentCaptor.forClass(WorkerMessage.class);
        verify(messageSender).send(eq(worker.getId()), sent.capture());
        assertThat(sent.getValue().type()).isEqualTo(WorkerMessageType.JOB_CANCEL);
        assertThat(sent.getValue().jobId()).isEqualTo(String.valueOf(job.getId()));
        assertThat(sent.getValue().payload().path("reason").asString()).isEqualTo("ACCEPT_VALIDATION_FAILED");
    }

    @Test
    @DisplayName("operatorAddress 가 등록된 워커 주소와 다르면 같은 fail closed")
    void accept_with_wrong_operator() {
        boolean started = jobService.acceptJob(worker.getId(), job.getId(), "17", "0x" + "f".repeat(40), "0xaccept");

        assertThat(started).isFalse();
        Job stopped = reload();
        assertThat(stopped.getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(stopped.getReconciliationReason()).contains("operatorAddress mismatch");
        verify(messageSender).send(eq(worker.getId()), any());
    }

    // ── JOB_RESULT ──────────────────────────────────────────────

    @Test
    @DisplayName("resultHash 가 §18 검증 sha256 에 0x 를 붙인 값과 같아야 COMPLETED")
    void complete_when_hash_matches() {
        makeRunning();
        jobRepository.findById(job.getId()).orElseThrow()
                .recordResultUpload("jobs/" + job.getId() + "/result.tar", 100L, SHA256);
        em.flush();

        boolean completed = jobService.completeJob(worker.getId(), job.getId(),
                "jobs/" + job.getId() + "/result.tar", "0x" + SHA256.toUpperCase(), "0xsubmit", 982.4);

        assertThat(completed).isTrue();
        Job done = reload();
        assertThat(done.getStatus()).isEqualTo(JobStatus.COMPLETED);
        assertThat(done.getSubmitTxHash()).isEqualTo("0xsubmit");
        assertThat(done.getReconciliationReason()).isNull();
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);
    }

    @Test
    @DisplayName("resultHash 가 다르면 fail closed — RUNNING 유지, 워커도 놓아주지 않고 대조 사유만 남긴다")
    void complete_with_wrong_hash() {
        makeRunning();
        jobRepository.findById(job.getId()).orElseThrow()
                .recordResultUpload("jobs/" + job.getId() + "/result.tar", 100L, SHA256);
        em.flush();
        workerRepository.findById(worker.getId()).orElseThrow().startJob(job.getId());
        em.flush();

        boolean completed = jobService.completeJob(worker.getId(), job.getId(),
                "jobs/" + job.getId() + "/result.tar", "0x" + "0".repeat(64), "0xsubmit", 982.4);

        assertThat(completed).isFalse();
        Job still = reload();
        assertThat(still.getStatus()).isEqualTo(JobStatus.RUNNING);
        assertThat(still.getReconciliationReason()).contains("resultHash mismatch");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.RUNNING);
    }

    @Test
    @DisplayName("§18 완료 검증을 거치지 않은 JOB_RESULT 는 받지 않는다")
    void complete_without_upload_verification() {
        makeRunning();

        boolean completed = jobService.completeJob(worker.getId(), job.getId(),
                "jobs/" + job.getId() + "/result.tar", "0x" + SHA256, "0xsubmit", 982.4);

        assertThat(completed).isFalse();
        Job still = reload();
        assertThat(still.getStatus()).isEqualTo(JobStatus.RUNNING);
        assertThat(still.getReconciliationReason()).contains("검증 기록 없음");
    }

    // ── JOB_REJECTED ────────────────────────────────────────────

    @Test
    @DisplayName("BLOCKCHAIN_RPC_FAILED 만 FUNDED 로 되돌려 재매칭한다")
    void reject_rpc_failed_is_retried() {
        assertThat(jobService.rejectJob(worker.getId(), job.getId(), "BLOCKCHAIN_RPC_FAILED", "rpc down")).isTrue();

        Job requeued = reload();
        assertThat(requeued.getStatus()).isEqualTo(JobStatus.FUNDED);
        assertThat(requeued.getAssignedWorkerId()).isNull();
        assertThat(requeued.getReconciliationReason()).isNull();
    }

    @Test
    @DisplayName("무결성 불일치·ESCROW_JOB_NOT_FOUND·ASSIGNMENT_MISMATCH 는 재매칭하지 않고 FAILED + 대조 사유")
    void reject_other_codes_fail_closed() {
        for (String code : new String[]{"PRICE_MISMATCH", "ESCROW_JOB_NOT_FOUND", "ASSIGNMENT_MISMATCH"}) {
            Job fresh = jobRepository.save(Job.create(client, job.getWorkloadId(), job.getBaseModelId(),
                    job.getDatasetId(), "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", worker.getId(), 9L));
            em.flush();
            jobRepository.fundIfCreated(fresh.getId(), 31221L, null, "18", null);
            jobRepository.assignIfFunded(fresh.getId(), worker.getId());

            assertThat(jobService.rejectJob(worker.getId(), fresh.getId(), code, "…")).as(code).isTrue();

            em.flush();
            em.clear();
            Job failed = jobRepository.findById(fresh.getId()).orElseThrow();
            assertThat(failed.getStatus()).as(code).isEqualTo(JobStatus.FAILED);
            assertThat(failed.getFailReason()).startsWith(code);
            assertThat(failed.getReconciliationReason()).as(code).contains("JOB_REJECTED " + code);
        }
    }

    // ── 취소 ────────────────────────────────────────────────────

    @Test
    @DisplayName("RUNNING 취소는 CANCELLING + JOB_CANCEL(USER_REQUEST), JOB_CANCELLED 가 와야 CANCELLED 로 확정")
    void cancel_running_goes_through_cancelling() {
        makeRunning();
        workerRepository.findById(worker.getId()).orElseThrow().startJob(job.getId());
        em.flush();

        jobService.cancelJob(client.getId(), job.getId());

        assertThat(reload().getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(workerStatus()).as("워커가 답하기 전에는 놓아주지 않는다").isEqualTo(WorkerStatus.RUNNING);
        ArgumentCaptor<WorkerMessage> sent = ArgumentCaptor.forClass(WorkerMessage.class);
        verify(messageSender).send(eq(worker.getId()), sent.capture());
        assertThat(sent.getValue().type()).isEqualTo(WorkerMessageType.JOB_CANCEL);
        assertThat(sent.getValue().payload().path("reason").asString()).isEqualTo("USER_REQUEST");

        assertThat(jobService.confirmCancelled(worker.getId(), job.getId(), "0xabort", 512.3)).isTrue();

        Job cancelled = reload();
        assertThat(cancelled.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(cancelled.getAbortTxHash()).isEqualTo("0xabort");
        assertThat(workerStatus()).isEqualTo(WorkerStatus.ONLINE);
    }

    @Test
    @DisplayName("FUNDED 취소 — 환불 미확인이면 CANCELLING(환불 대기), JOB_CANCEL 보낼 상대는 없다")
    void cancel_funded_waits_for_refund() {
        jobRepository.unassignIfAssigned(job.getId(), worker.getId());   // 다시 FUNDED
        em.clear();
        // 기본 UNKNOWN(아무것도 확인 안 됨) — 예치금이 잠겨 있을 수 있으므로 종료하지 않고
        // keeper 순찰에 남긴다. 체인 CANCELLED 확인 시 즉시 종료는 JobCancelReconciliationTest 가 검증.

        jobService.cancelJob(client.getId(), job.getId());

        assertThat(reload().getStatus()).isEqualTo(JobStatus.CANCELLING);
        verify(messageSender, never()).send(any(), any());   // 배정 전이라 워커 통지 없음
    }
}
