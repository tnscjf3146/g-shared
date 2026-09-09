package com.gshared.back.job.service;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.worker.websocket.WorkerMessageSender;
import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.OffsetDateTime;
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
    private final JobService jobService;
    private final WorkerRepository workerRepository;
    private final WorkerMessageSender messageSender;
    private final ObjectMapper objectMapper;

    /**
     * 의도적으로 메서드 트랜잭션 없음 — Job 하나당 assignIfFunded(자체 트랜잭션 커밋) 후 전송.
     * 커밋 후 전송이라 "워커는 받았는데 DB 는 미배정" 불일치가 없고,
     * Job 별 try/catch 로 깨진 Job 하나가 순찰 전체를 마비시키지 않는다(poison 격리).
     */
    @Scheduled(fixedDelay = 30_000)
    public void match() {
        List<Job> waiting = jobRepository.findByStatusOrderByCreatedAtAsc(JobStatus.FUNDED);
        if (waiting.isEmpty()) {
            return;   // 배정할 주문 없음 → 조용히 종료
        }

        List<Worker> available = workerRepository.findAvailableWorkers();
        if (available.isEmpty()) {
            log.info("[MATCH] 가용 워커 없음 — 대기 Job {}건은 다음 순찰에 재시도", waiting.size());
            return;
        }
        java.util.Map<String, Worker> availableById = new java.util.HashMap<>();
        available.forEach(w -> availableById.put(w.getId(), w));
        java.util.Set<String> takenThisSweep = new java.util.HashSet<>();   // 이번 순찰에서 이미 배정된 워커

        for (Job job : waiting) {
            Worker worker = pickWorker(job, availableById, takenThisSweep);
            if (worker == null) {
                // 추천 워커가 바쁘거나 오프라인 → 배정 보류, 다음 순찰에 재시도
                // TODO: N회 연속 보류 시 재예측 (1 Job = 1 Prediction 은 이중 계산 방지 취지일 뿐)
                log.info("[MATCH] jobId={} 추천 워커({}) 사용 불가 — 다음 순찰 재시도",
                        job.getId(), job.getRecommendedWorkerId());
                continue;
            }
            try {
                // "아직 FUNDED 일 때만" 배정(서비스 트랜잭션) — 그 사이 취소됐으면 건너뜀 (취소 되살림 방지)
                if (!jobService.assignJob(job.getId(), worker.getId())) {
                    log.info("[MATCH] jobId={} 상태 변경됨(취소 등) — 건너뜀", job.getId());
                    continue;
                }
                takenThisSweep.add(worker.getId());
                boolean sent = messageSender.send(worker.getId(), buildAssignMessage(job));
                log.info("[MATCH] jobId={} → workerId={} (JOB_ASSIGN 전송={})", job.getId(), worker.getId(), sent);
                if (!sent) {
                    // 죽은 워커(스위퍼 처리 전)에 배정된 경우 → 즉시 FUNDED 복귀, 다음 순찰에 재매칭
                    jobService.unassignJob(job.getId(), worker.getId());
                    takenThisSweep.remove(worker.getId());   // 배정 되돌렸으니 이번 순찰 점유도 해제
                    log.info("[MATCH] jobId={} 전송 실패 → 배정 되돌림(재매칭 대기)", job.getId());
                }
                // TODO: 전송은 됐지만 JOB_ACCEPTED 가 오래 안 오는 Job 재큐잉은 후속 티켓
            } catch (Exception e) {
                log.error("[MATCH] jobId={} 매칭 실패(다음 Job 계속)", job.getId(), e);
            }
        }
    }

    /**
     * 배정할 워커 선택 — Scheduler 추천 우선.
     * 추천 워커가 지금 가용하면 그 워커, 아니면 null(보류 → 다음 순찰).
     * 추천이 없는 Job(구버전 데이터 등)만 아무 가용 워커로 폴백.
     */
    private Worker pickWorker(Job job, java.util.Map<String, Worker> availableById,
                              java.util.Set<String> takenThisSweep) {
        String recommended = job.getRecommendedWorkerId();
        if (recommended != null) {
            Worker w = availableById.get(recommended);
            return (w != null && !takenThisSweep.contains(recommended)) ? w : null;
        }
        return availableById.values().stream()
                .filter(w -> !takenThisSweep.contains(w.getId()))
                .findFirst()
                .orElse(null);
    }

    /**
     * JOB_ASSIGN 봉투 생성. payload 는 이형동 명세 기준.
     * TODO: dockerImageId 는 workloads.image_digest 로 교체 (실제 digest 를 System 담당에게 받은 뒤)
     *       datasetUrl·resultUploadUrl 은 스토리지 정책 확정 후 추가.
     */
    private WorkerMessage buildAssignMessage(Job job) {
        ObjectNode payload = objectMapper.createObjectNode();
        payload.put("dockerImageId", job.getWorkloadId());   // "qlora-sft-v1"
        payload.put("modelId", job.getBaseModelId());        // "qwen2.5-7b"
        payload.put("datasetId", job.getDatasetId());
        payload.put("trainingMode", job.getTrainingMode());

        ObjectNode config = payload.putObject("trainingConfig");
        config.put("epochs", job.getEpochs());
        config.put("batchSize", job.getBatchSize());
        config.put("learningRate", job.getLearningRate());
        config.put("maxLength", job.getMaxLength());

        return WorkerMessage.of(WorkerMessageType.JOB_ASSIGN,
                String.valueOf(job.getId()),          // 명세상 jobId 는 문자열
                OffsetDateTime.now().toString(),
                payload);
    }
}
