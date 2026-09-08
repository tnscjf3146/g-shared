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
        int pairs = Math.min(waiting.size(), available.size());

        for (int i = 0; i < pairs; i++) {
            Job job = waiting.get(i);
            Worker worker = available.get(i);
            try {
                // "아직 FUNDED 일 때만" 배정 — 그 사이 취소됐으면 0행으로 건너뜀 (취소 되살림 방지)
                if (jobRepository.assignIfFunded(job.getId(), worker.getId()) == 0) {
                    log.info("[MATCH] jobId={} 상태 변경됨(취소 등) — 건너뜀", job.getId());
                    continue;
                }
                boolean sent = messageSender.send(worker.getId(), buildAssignMessage(job));
                log.info("[MATCH] jobId={} → workerId={} (JOB_ASSIGN 전송={})", job.getId(), worker.getId(), sent);
                // 전송 실패(그 찰나 연결 끊김)여도 ASSIGNED 유지 — 워커는 스위퍼가 OFFLINE 처리.
                // TODO: ASSIGNED 인데 오래 JOB_ACCEPTED 없는 Job 재큐잉(FUNDED 복귀)은 후속 티켓
            } catch (Exception e) {
                log.error("[MATCH] jobId={} 매칭 실패(다음 Job 계속)", job.getId(), e);
            }
        }

        if (waiting.size() > pairs) {
            log.info("[MATCH] 워커 부족 — 대기 Job {}건은 다음 순찰에 재시도", waiting.size() - pairs);
        }
    }

    /**
     * JOB_ASSIGN 봉투 생성. payload 는 이형동 명세 기준.
     * dockerImageId·datasetUrl·resultUploadUrl 은 Workload/Dataset 도메인 구현 후 채움(현재는 ID 전달).
     */
    private WorkerMessage buildAssignMessage(Job job) {
        ObjectNode payload = objectMapper.createObjectNode();
        payload.put("baseModelId", job.getBaseModelId());
        payload.put("datasetId", job.getDatasetId());
        payload.put("gpuId", job.getGpuId());
        payload.put("trainingMode", job.getTrainingMode());
        if (job.getTrainingConfig() != null) {
            payload.set("trainingConfig", objectMapper.readTree(job.getTrainingConfig()));   // 문자열 → JSON 복원
        }
        return WorkerMessage.of(WorkerMessageType.JOB_ASSIGN,
                String.valueOf(job.getId()),          // 명세상 jobId 는 문자열
                OffsetDateTime.now().toString(),
                payload);
    }
}
