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
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen2.5-7b").orElseThrow().getId();
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
        return workerRepository.save(Worker.register(
                "worker-test-" + suffix, owner, "hash-" + suffix,
                "테스트PC", "Windows 11", "RTX 4070", 12000, "551.23", 42.5));
    }

    // ── cancelIfCancellable ─────────────────────────────────────

    @Test
    @DisplayName("취소: CREATED·FUNDED·ASSIGNED 는 성공한다")
    void cancel_allowed_states() {
        for (JobStatus from : List.of(JobStatus.CREATED, JobStatus.FUNDED, JobStatus.ASSIGNED)) {
            Job job = newJob();
            setState(job.getId(), from, null);

            int updated = jobRepository.cancelIfCancellable(job.getId(), owner.getId(), NOW);

            assertThat(updated).as("%s 에서 취소", from).isEqualTo(1);
            assertThat(statusOf(job.getId())).isEqualTo(JobStatus.CANCELLED);
        }
    }

    @Test
    @DisplayName("취소: RUNNING 이후는 0행 — 실행 중인 작업을 되돌리지 않는다")
    void cancel_running_is_rejected() {
        for (JobStatus from : List.of(JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED)) {
            Job job = newJob();
            setState(job.getId(), from, workerA.getId());

            assertThat(jobRepository.cancelIfCancellable(job.getId(), owner.getId(), NOW))
                    .as("%s 에서 취소 시도", from).isZero();
            assertThat(statusOf(job.getId())).isEqualTo(from);   // 상태 유지
        }
    }

    @Test
    @DisplayName("취소: 중복 취소는 두 번째가 0행")
    void cancel_twice_second_is_zero() {
        Job job = newJob();

        assertThat(jobRepository.cancelIfCancellable(job.getId(), owner.getId(), NOW)).isEqualTo(1);
        assertThat(jobRepository.cancelIfCancellable(job.getId(), owner.getId(), NOW)).isZero();
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

        assertThat(jobRepository.startIfAssigned(job.getId(), workerA.getId(), NOW)).isEqualTo(1);

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

        assertThat(jobRepository.startIfAssigned(job.getId(), workerB.getId(), NOW)).isZero();   // 남의 워커
        assertThat(jobRepository.startIfAssigned(job.getId(), workerA.getId(), NOW)).isEqualTo(1);
        assertThat(jobRepository.startIfAssigned(job.getId(), workerA.getId(), NOW)).isZero();   // 중복
    }

    // ── completeIfRunning / failIfRunning ───────────────────────

    @Test
    @DisplayName("완료: 그 워커의 RUNNING 만 COMPLETED + 결과 기록")
    void complete_by_running_worker() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        int updated = jobRepository.completeIfRunning(job.getId(), workerA.getId(),
                "sha256:abc", "https://storage/result", 1480.5, NOW);

        assertThat(updated).isEqualTo(1);
        em.clear();
        Job done = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(done.getStatus()).isEqualTo(JobStatus.COMPLETED);
        assertThat(done.getResultHash()).isEqualTo("sha256:abc");
        assertThat(done.getActualRuntimeSec()).isEqualTo(1480.5);
    }

    @Test
    @DisplayName("완료·실패: 엉뚱한 워커는 0행")
    void complete_and_fail_wrong_worker_rejected() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        assertThat(jobRepository.completeIfRunning(job.getId(), workerB.getId(), "h", "u", 1.0, NOW)).isZero();
        assertThat(jobRepository.failIfRunning(job.getId(), workerB.getId(), "GPU_OOM", NOW)).isZero();
        assertThat(statusOf(job.getId())).isEqualTo(JobStatus.RUNNING);
    }

    @Test
    @DisplayName("실패: RUNNING 만 FAILED + 사유 기록")
    void fail_by_running_worker() {
        Job job = newJob();
        setState(job.getId(), JobStatus.RUNNING, workerA.getId());

        assertThat(jobRepository.failIfRunning(job.getId(), workerA.getId(), "GPU_OOM: out of memory", NOW))
                .isEqualTo(1);
        em.clear();
        Job failed = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(failed.getStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(failed.getFailReason()).isEqualTo("GPU_OOM: out of memory");
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
}
