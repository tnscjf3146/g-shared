package com.gshared.back.job.service;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Job 매칭 순찰자(중매쟁이).
 * 30초마다: 배정 대기(FUNDED) Job ↔ 배정 가능한(ONLINE·일 없는) Worker 를 선착순으로 짝짓는다.
 *
 * <p>즉시 매칭 대신 순찰 방식인 이유: 그 순간 워커가 없어도 다음 순찰에 자동 재시도 →
 * 별도 재시도 로직이 필요 없다. (하트비트 스위퍼와 같은 패턴)
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class JobMatcher {

    private final JobRepository jobRepository;
    private final WorkerRepository workerRepository;

    @Scheduled(fixedDelay = 30_000)
    @Transactional
    public void match() {
        List<Job> waiting = jobRepository.findByStatusOrderByCreatedAtAsc(JobStatus.FUNDED);
        if (waiting.isEmpty()) {
            return;   // 배정할 주문 없음 → 조용히 종료
        }

        List<Worker> available = workerRepository.findAvailableWorkers();
        int pairs = Math.min(waiting.size(), available.size());

        for (int i = 0; i < pairs; i++) {
            Job job = waiting.get(i);
            Worker worker = available.get(i);
            job.assign(worker.getId());   // FUNDED → ASSIGNED (더티체킹으로 자동 저장)
            log.info("[MATCH] jobId={} → workerId={}", job.getId(), worker.getId());
            // TODO(-120): 워커에게 JOB_ASSIGN WebSocket 전송
        }

        if (waiting.size() > pairs) {
            log.info("[MATCH] 워커 부족 — 대기 Job {}건은 다음 순찰에 재시도", waiting.size() - pairs);
        }
    }
}
