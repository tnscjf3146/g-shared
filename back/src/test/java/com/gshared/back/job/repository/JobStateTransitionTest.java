package com.gshared.back.job.repository;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Job 상태전이(조건부 UPDATE 6종)와 findAvailableWorkers 검증.
 * 동시성 안전의 근거인 "조건 불일치 = 0행"이 정확히 동작하는지가 핵심.
 * 테스트 트랜잭션 롤백으로 DB 흔적 없음.
 */
@SpringBootTest
@Transactional
class JobStateTransitionTest {

    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private EntityManager em;

    private static final LocalDateTime NOW = LocalDateTime.of(2026, 9, 9, 12, 0);

    @Autowired
    private DatasetRepository datasetRepository;

    @Autowired
    private com.gshared.back.workload.repository.WorkloadRepository workloadRepository;
    @Autowired
    private com.gshared.back.workload.repository.BaseModelRepository baseModelRepository;

    private User owner;
    private Dataset dataset;      // FK 만족용 실제 데이터셋
    private Worker workerA;       // FK 만족용 실제 워커 2명
    private Worker workerB;
    private Long workloadId;      // V8 seed 카탈로그의 정수 PK (auto-increment 값에 의존하지 않게 slug 로 조회)
    private Long baseModelId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "transition-" + UUID.randomUUID() + "@gshared.io", "encoded", "전이테스터"));
        owner.linkWallet("0x" + "a".repeat(40));   // provider 정산 주소 — 없으면 워커가 후보에 오르지 못한다
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();
        dataset = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null));
        workerA = newWorker("a-" + UUID.randomUUID());
        workerB = newWorker("b-" + UUID.randomUUID());
    }

    // ── 헬퍼 ────────────────────────────────────────────────────

    private Job newJob() {
        return jobRepository.save(Job.create(owner, workloadId, baseModelId, dataset.getId(),
                "AUTO", 3, 8, 0.0002, 1024, 1500.0, "runtime-xgb-v1", null, 3000L));
    }

    /** 테스트용 상태 강제 세팅 (엔티티에 setter 가 없으므로 JPQL 로 직접). */
    private void setState(Long jobId, JobStatus status, String workerId) {
        em.flush();   // clear 전에 대기 중인 변경(goOnline 등)을 DB 로 먼저 반영 — 안 하면 clear 때 증발
        em.createQuery("UPDATE Job j SET j.status = :s, j.assignedWorkerId = :w WHERE j.id = :id")
                .setParameter("s", status)
                .setParameter("w", workerId)
                .setParameter("id", jobId)
                .executeUpdate();
        em.clear();   // 1차 캐시 비움 — 이후 조회가 DB 최신값을 읽게
    }

    private JobStatus statusOf(Long jobId) {
        em.clear();
        return jobRepository.findById(jobId).orElseThrow().getStatus();
    }

    private Worker newWorker(String suffix) {
        return newWorker(suffix, "0x" + "b".repeat(40));
    }

    /** operatorAddress 를 고를 수 있는 버전 — null 이면 "서명 주소 없는 워커" 케이스. */
    private Worker newWorker(String suffix, String operatorAddress) {
        return workerRepository.save(Worker.register(
                "worker-test-" + suffix, owner, "hash-" + suffix, operatorAddress, "0.1.0",
                "Windows 11", true, "RTX 4070", 12000, 42.5, null, "transformer-bench-v1"));
    }

    // ── cancelIfUnassigned / markCancellingIfActive ─────────────

    @Test
    @DisplayName("취소 ①: 워커 손에 가기 전(CREATED·FUNDED)은 바로 CANCELLED")
    void cancel_unassigned_states() {
        for (JobStatus from : List.of(JobStatus.CREATED, JobStatus.FUNDED)) {
            Job job = newJob();
            setState(job.getId(), from, null);

            assertThat(jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), NOW))
                    .as("%s 에서 취소", from).isEqualTo(1);
            assertThat(statusOf(job.getId())).isEqualTo(JobStatus.CANCELLED);
        }
    }

    @Test
    @DisplayName("취소 ②: 워커 손에 있는 Job(ASSIGNED·RUNNING)은 CANCELLING 까지만 — 확정은 JOB_CANCELLED 가 한다")
    void cancel_active_states_become_cancelling() {
        for (JobStatus from : List.of(JobStatus.ASSIGNED, JobStatus.RUNNING)) {
            Job job = newJob();
            setState(job.getId(), from, workerA.getId());

            assertThat(jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), NOW))
                    .as("%s 는 ①로 바로 취소되지 않는다", from).isZero();
            assertThat(jobRepository.markCancellingIfActive(job.getId(), owner.getId()))
                    .as("%s → CANCELLING", from).isEqualTo(1);
            assertThat(statusOf(job.getId())).isEqualTo(JobStatus.CANCELLING);
        }
    }

    @Test
    @DisplayName("취소: 종료 상태(COMPLETED/FAILED/CANCELLED)와 CANCELLING 은 두 UPDATE 모두 0행")
    void cancel_finished_is_rejected() {
        for (JobStatus from : List.of(JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.CANCELLING)) {
            Job job = newJob();
            setState(job.getId(), from, workerA.getId());

            assertThat(jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), NOW))
                    .as("%s 에서 취소 ①", from).isZero();
            assertThat(jobRepository.markCancellingIfActive(job.getId(), owner.getId()))
                    .as("%s 에서 취소 ②", from).isZero();
            assertThat(statusOf(job.getId())).isEqualTo(from);   // 상태 유지
        }
    }

    @Test
    @DisplayName("취소: 중복 취소는 두 번째가 0행")
    void cancel_twice_second_is_zero() {
        Job job = newJob();

        assertThat(jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), NOW)).isEqualTo(1);
        assertThat(jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), NOW)).isZero();
    }

    @Test
    @DisplayName("fail closed: 배정된 워커의 ASSIGNED Job 만 CANCELLING 으로 — RUNNING 은 건드리지 않는다")
    void mark_cancelling_by_worker_only_when_assigned() {
        Job assigned = newJob();
        setState(assigned.getId(), JobStatus.ASSIGNED, workerA.getId());
        assertThat(jobRepository.markCancellingIfAssigned(assigned.getId(), workerB.getId())).as("남의 워커").isZero();
        assertThat(jobRepository.markCancellingIfAssigned(assigned.getId(), workerA.getId())).isEqualTo(1);
        assertThat(statusOf(assigned.getId())).isEqualTo(JobStatus.CANCELLING);

        Job running = newJob();
        setState(running.getId(), JobStatus.RUNNING, workerA.getId());
        assertThat(jobRepository.markCancellingIfAssigned(running.getId(), workerA.getId())).isZero();
    }

    // ── assignIfFunded ──────────────────────────────────────────

    @Test
    @DisplayName("배정: FUNDED 만 ASSIGNED 로 전이하고 워커가 기록된다")
    void assign_funded_only() {
        Job job = newJob();
        setState(job.getId(), JobStatus.FUNDED, null);

        assertThat(jobRepository.assignIfFunded(job.getId(), workerA.getId())).isEqualTo(1);

        em.clear();
        Job assigned = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(assigned.getStatus()).isEqualTo(JobStatus.ASSIGNED);
        assertThat(assigned.getAssignedWorkerId()).isEqualTo(workerA.getId());
    }

    @Test
    @DisplayName("배정: 그 사이 취소된 Job 은 0행 — 취소를 되살리지 않는다")
    void assign_cancelled_is_rejected() {
        Job job = newJob();
        setState(job.getId(), JobStatus.CANCELLED, null);

        assertThat(jobRepository.assignIfFunded(job.getId(), workerA.getId())).isZero();
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.CANCELLED);
    }

    // ── unassignIfAssigned ──────────────────────────────────────

    @Test
    @DisplayName("배정 되돌리기: 그 워커의 ASSIGNED 만 FUNDED 로 복귀한다")
    void unassign_returns_to_funded() {
        Job job = newJob();
        setState(job.getId(), JobStatus.ASSIGNED, workerA.getId());

        assertThat(jobRepository.unassignIfAssigned(job.getId(), workerA.getId())).isEqualTo(1);

        em.clear();
        Job reverted = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(reverted.getStatus()).isEqualTo(JobStatus.FUNDED);
        assertThat(reverted.getAssignedWorkerId()).isNull();
    }

    @Test
    @DisplayName("배정 되돌리기: RUNNING 이거나 다른 워커면 0행")
    void unassign_rejected_cases() {
        Job running = newJob();
        setState(running.getId(), JobStatus.RUNNING, workerA.getId());
        assertThat(jobRepository.unassignIfAssigned(running.getId(), workerA.getId())).isZero();

        Job othersAssign = newJob();
        setState(othersAssign.getId(), JobStatus.ASSIGNED, workerA.getId());
        assertThat(jobRepository.unassignIfAssigned(othersAssign.getId(), workerB.getId())).isZero();
        assertThat(statusOf(othersAssign.getId())).isEqualTo(JobStatus.ASSIGNED);
    }

    // ── startIfAssigned ─────────────────────────────────────────

    @Test
    @DisplayName("시작: 배정된 워커의 JOB_ACCEPTED 만 RUNNING 이 된다")
    void start_by_assigned_worker() {
        Job job = newJob();
        setState(job.getId(), JobStatus.ASSIGNED, workerA.getId());

        assertThat(jobRepository.startIfAssigned(job.getId(), workerA.getId(), "0xaccept", NOW)).isEqualTo(1);

        em.clear();
        Job started = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(started.getStatus()).isEqualTo(JobStatus.RUNNING);
        assertThat(started.getStartedAt()).isEqualTo(NOW);
    }

    @Test
    @DisplayName("시작: 엉뚱한 워커·중복 메시지는 0행")
    void start_rejected_cases() {
        Job job = newJob();
        setState(job.getId(), JobStatus.ASSIGNED, workerA.getId());

        assertThat(jobRepository.startIfAssigned(job.getId(), workerB.getId(), "0xaccept", NOW)).isZero();   // 남의 워커
        assertThat(jobRepository.startIfAssigned(job.getId(), workerA.getId(), "0xaccept", NOW)).isEqualTo(1);
        assertThat(jobRepository.startIfAssigned(job.getId(), workerA.getId(), "0xaccept", NOW)).isZero();   // 중복
    }

    // ── completeIfRunning / failIfRunning ───────────────────────

    @Test
    @DisplayName("완료: 그 워커의 RUNNING 만 COMPLETED + 결과 기록")
    void complete_by_running_worker() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        int updated = jobRepository.completeIfRunning(job.getId(), workerA.getId(),
                "jobs/1/result.tar", "0xabc", "0xsubmit", 1480.5, NOW);

        assertThat(updated).isEqualTo(1);
        em.clear();
        Job done = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(done.getStatus()).isEqualTo(JobStatus.COMPLETED);
        assertThat(done.getResultHash()).isEqualTo("0xabc");
        assertThat(done.getResultObjectKey()).isEqualTo("jobs/1/result.tar");
        assertThat(done.getSubmitTxHash()).isEqualTo("0xsubmit");
        assertThat(done.getActualRuntimeSec()).isEqualTo(1480.5);
    }

    @Test
    @DisplayName("완료·실패: 엉뚱한 워커는 0행")
    void complete_and_fail_wrong_worker_rejected() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        assertThat(jobRepository.completeIfRunning(job.getId(), workerB.getId(), "k", "h", "t", 1.0, NOW)).isZero();
        assertThat(jobRepository.failIfRunning(job.getId(), workerB.getId(), "CUDA_OOM", "0xabort", 1.0, NOW)).isZero();
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.RUNNING);
    }

    @Test
    @DisplayName("실패: RUNNING 만 FAILED + 사유 기록")
    void fail_by_running_worker() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        assertThat(jobRepository.failIfRunning(job.getId(), workerA.getId(),
                "CUDA_OOM: CUDA out of memory.", "0xabort", 124.2, NOW)).isEqualTo(1);
        em.clear();
        Job failed = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(failed.getStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(failed.getFailReason()).isEqualTo("CUDA_OOM: CUDA out of memory.");
        assertThat(failed.getAbortTxHash()).isEqualTo("0xabort");
        assertThat(failed.getActualRuntimeSec()).isEqualTo(124.2);
    }

    // ── rejectIfAssigned / confirmCancelledIfCancelling ─────────

    @Test
    @DisplayName("거절: 배정된 워커의 JOB_REJECTED 만 FAILED + 사유 기록, RUNNING 이면 0행")
    void reject_by_assigned_worker_only() {
        Job job = newJob();
        setState(job.getId(), JobStatus.ASSIGNED, workerA.getId());

        assertThat(jobRepository.rejectIfAssigned(job.getId(), workerB.getId(), "PRICE_MISMATCH", NOW)).isZero();
        assertThat(jobRepository.rejectIfAssigned(job.getId(), workerA.getId(),
                "PRICE_MISMATCH: On-chain price does not match backend assignment.", NOW)).isEqualTo(1);

        em.clear();
        Job rejected = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(rejected.getStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(rejected.getFailReason()).startsWith("PRICE_MISMATCH");

        Job running = newJob();
        setState(running.getId(), JobStatus.RUNNING, workerA.getId());
        assertThat(jobRepository.rejectIfAssigned(running.getId(), workerA.getId(), "X", NOW)).isZero();
    }

    @Test
    @DisplayName("취소 확인: CANCELLING 인 그 워커의 Job 만 CANCELLED 로 확정 + abortTxHash·실측 시간 기록")
    void confirm_cancelled_only_when_cancelling() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());
        assertThat(jobRepository.confirmCancelledIfCancelling(job.getId(), workerA.getId(), "0xabort", 512.3, NOW))
                .as("취소 요청 전 — RUNNING 은 확정 대상이 아니다").isZero();

        setState(job.getId(), JobStatus.CANCELLING, workerA.getId());
        assertThat(jobRepository.confirmCancelledIfCancelling(job.getId(), workerB.getId(), "0xabort", 512.3, NOW))
                .as("남의 워커").isZero();
        assertThat(jobRepository.confirmCancelledIfCancelling(job.getId(), workerA.getId(), "0xabort", 512.3, NOW))
                .isEqualTo(1);
        assertThat(jobRepository.confirmCancelledIfCancelling(job.getId(), workerA.getId(), "0xabort", 512.3, NOW))
                .as("중복").isZero();

        em.clear();
        Job cancelled = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(cancelled.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(cancelled.getAbortTxHash()).isEqualTo("0xabort");
        assertThat(cancelled.getActualRuntimeSec()).isEqualTo(512.3);
        assertThat(cancelled.getFinishedAt()).isEqualTo(NOW);
    }


    @Test
    @DisplayName("예치 확인: CREATED 만 FUNDED 로 전이하고 온체인 jobId 를 기록한다")
    void fund_records_escrow_job_id() {
        Job job = newJob();

        assertThat(jobRepository.fundIfCreated(job.getId(), 31221L, null, "17", null)).isEqualTo(1);
        assertThat(jobRepository.fundIfCreated(job.getId(), 31221L, null, "18", null)).as("중복").isZero();

        em.clear();
        Job funded = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(funded.getStatus()).isEqualTo(JobStatus.FUNDED);
        assertThat(funded.getEscrowJobId()).isEqualTo("17");
        assertThat(funded.getExternalRef()).matches("^0x[0-9a-f]{64}$");   // 생성 시 발급된 bytes32
    }

    // ── 소유권 ──────────────────────────────────────────────────

    @Test
    @DisplayName("소유권: 남의 Job 은 findByIdAndOwnerId 에 잡히지 않는다")
    void ownership_query_hides_others_job() {
        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "타인"));
        Job othersJob = jobRepository.save(Job.create(other, workloadId, baseModelId, dataset.getId(),
                "AUTO", 3, 8, 0.0002, 1024, 1500.0, "runtime-xgb-v1", null, 3000L));

        assertThat(jobRepository.findByIdAndOwnerId(othersJob.getId(), owner.getId())).isEmpty();
        assertThat(jobRepository.findByIdAndOwnerId(othersJob.getId(), other.getId())).isPresent();
    }

    // ── findAvailableWorkers ────────────────────────────────────

    @Test
    @DisplayName("가용 워커: ONLINE 이면서 맡은 Job 없는 워커만 잡힌다")
    void available_workers_filter() {
        Worker online = newWorker("online");
        online.goOnline(NOW);                       // ONLINE, 일 없음 → 후보 ✅

        Worker offline = newWorker("offline");      // OFFLINE → 제외

        Worker busy = newWorker("busy");
        busy.goOnline(NOW);                         // ONLINE 이지만...
        Job busyJob = newJob();
        setState(busyJob.getId(), JobStatus.ASSIGNED, busy.getId());   // ...배정된 Job 있음 → 제외

        List<Worker> available = workerRepository.findAvailableWorkers();
        List<String> ids = available.stream().map(Worker::getId).toList();

        assertThat(ids).contains(online.getId());
        assertThat(ids).doesNotContain(offline.getId(), busy.getId());
    }

    @Test
    @DisplayName("가용 워커: operator 주소나 주인 지갑이 없으면 ONLINE 이어도 후보가 아니다 (규격 §28 operator 조건)")
    void available_workers_require_escrow_addresses() {
        Worker noOperator = newWorker("no-operator", null);
        noOperator.goOnline(NOW);

        User walletless = userRepository.save(User.createUser(
                "walletless-" + UUID.randomUUID() + "@gshared.io", "encoded", "지갑없음"));
        Worker noWallet = workerRepository.save(Worker.register(
                "worker-test-no-wallet", walletless, "hash-no-wallet", "0x" + "c".repeat(40), "0.1.0",
                "Windows 11", true, "RTX 4070", 12000, 42.5, null, "transformer-bench-v1"));
        noWallet.goOnline(NOW);

        Worker ready = newWorker("ready");
        ready.goOnline(NOW);

        List<String> ids = workerRepository.findAvailableWorkers().stream().map(Worker::getId).toList();

        assertThat(ids).contains(ready.getId());
        assertThat(ids).doesNotContain(noOperator.getId(), noWallet.getId());
    }

    // ── CANCELLING 과 겹친 종료 메시지 ───────────────────────────
    //
    // 사용자가 취소를 누른 순간 워커는 이미 결과를 올리고 온체인 submitResult 까지
    // 끝냈을 수 있다. RUNNING 만 받으면 그 JOB_RESULT 는 버려지고, 에스크로는
    // COMPLETED 라 공급자는 정산을 받았는데 Job 은 CANCELLING 에 영원히 남는다 --
    // 돈은 지불됐고 기록은 취소 중인 상태.

    @Test
    @DisplayName("취소 중에 도착한 JOB_RESULT 는 COMPLETED 로 확정된다")
    void completeIfCancelling_transitions() {
        Job job = newJob();
        setState(job.getId(), JobStatus.CANCELLING, workerA.getId());

        int updated = jobRepository.completeIfCancelling(job.getId(), workerA.getId(),
                "jobs/1/result.tar", "0x" + "ab".repeat(32), "0x" + "cd".repeat(32),
                982.4, "결과가 먼저 도착했다", NOW);

        assertThat(updated).isEqualTo(1);
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.COMPLETED);
    }

    @Test
    @DisplayName("그 사실이 reconciliationReason 에 남는다 -- 상태만 보면 취소 흔적이 사라진다")
    void completeIfCancelling_recordsWhy() {
        Job job = newJob();
        setState(job.getId(), JobStatus.CANCELLING, workerA.getId());

        jobRepository.completeIfCancelling(job.getId(), workerA.getId(),
                "jobs/1/result.tar", "0x" + "ab".repeat(32), "0x" + "cd".repeat(32),
                982.4, "결과가 먼저 도착했다", NOW);

        em.clear();
        assertThat(jobRepository.findById(job.getId()).orElseThrow()
                .getReconciliationReason()).isEqualTo("결과가 먼저 도착했다");
    }

    @Test
    @DisplayName("RUNNING 인 Job 은 completeIfCancelling 으로 바뀌지 않는다")
    void completeIfCancelling_ignoresRunning() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        int updated = jobRepository.completeIfCancelling(job.getId(), workerA.getId(),
                "jobs/1/result.tar", "0x" + "ab".repeat(32), null, 1.0, "x", NOW);

        assertThat(updated).isZero();
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.RUNNING);
    }

    @Test
    @DisplayName("남의 워커가 보낸 것은 취소 중이어도 무시된다")
    void completeIfCancelling_ignoresOtherWorker() {
        Job job = newJob();
        setState(job.getId(), JobStatus.CANCELLING, workerA.getId());

        int updated = jobRepository.completeIfCancelling(job.getId(), workerB.getId(),
                "jobs/1/result.tar", "0x" + "ab".repeat(32), null, 1.0, "x", NOW);

        assertThat(updated).isZero();
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.CANCELLING);
    }

    @Test
    @DisplayName("취소 중에 도착한 JOB_FAILED 는 FAILED 가 아니라 CANCELLED 다")
    void cancelIfFailedWhileCancelling_transitions() {
        // 워커가 실패를 보고했다는 것은 abortJob 으로 예치금을 돌려줬다는 뜻이고,
        // 체인은 CANCELLED 다. 사용자는 취소를 요청했고 돈은 돌아갔으므로
        // CANCELLED 가 양쪽과 맞는 유일한 종료 상태다.
        Job job = newJob();
        setState(job.getId(), JobStatus.CANCELLING, workerA.getId());

        int updated = jobRepository.cancelIfFailedWhileCancelling(job.getId(), workerA.getId(),
                "CUDA_OOM: the GPU ran out of memory", "0x" + "ef".repeat(32),
                41.2, "취소 중 실패", NOW);

        assertThat(updated).isEqualTo(1);
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.CANCELLED);
    }

    @Test
    @DisplayName("실패 사유는 버리지 않는다 -- 취소로 끝난 것과 무엇이 잘못됐는지는 다른 정보다")
    void cancelIfFailedWhileCancelling_keepsFailReason() {
        Job job = newJob();
        setState(job.getId(), JobStatus.CANCELLING, workerA.getId());

        jobRepository.cancelIfFailedWhileCancelling(job.getId(), workerA.getId(),
                "CUDA_OOM: the GPU ran out of memory", "0x" + "ef".repeat(32),
                41.2, "취소 중 실패", NOW);

        em.clear();
        Job settled = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(settled.getFailReason()).contains("CUDA_OOM");
        assertThat(settled.getAbortTxHash()).isEqualTo("0x" + "ef".repeat(32));
        assertThat(settled.getActualRuntimeSec()).isEqualTo(41.2);
    }

    @Test
    @DisplayName("RUNNING 인 Job 은 cancelIfFailedWhileCancelling 으로 바뀌지 않는다")
    void cancelIfFailedWhileCancelling_ignoresRunning() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        int updated = jobRepository.cancelIfFailedWhileCancelling(job.getId(), workerA.getId(),
                "X: y", null, 1.0, "x", NOW);

        assertThat(updated).isZero();
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.RUNNING);
    }

    @Test
    @DisplayName("이미 끝난 Job 은 어느 쪽으로도 다시 바뀌지 않는다")
    void terminalJobsStayTerminal() {
        for (JobStatus done : List.of(JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)) {
            Job job = newJob();
            setState(job.getId(), done, workerA.getId());

            assertThat(jobRepository.completeIfCancelling(job.getId(), workerA.getId(),
                    "k", "0x" + "ab".repeat(32), null, 1.0, "x", NOW)).isZero();
            assertThat(jobRepository.cancelIfFailedWhileCancelling(job.getId(), workerA.getId(),
                    "X: y", null, 1.0, "x", NOW)).isZero();
            assertThat(statusOf(job.getId())).isEqualTo(done);
        }
    }
}
