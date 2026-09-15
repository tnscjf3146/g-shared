package com.gshared.back.job.service;

import com.gshared.back.job.domain.Job;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 데이터셋 원본 파일 cleanup 순찰. 끝난 Job(COMPLETED/FAILED/CANCELLED)과 예치 없이 방치된 CREATED Job 의
 * 파일을 지운다 — 원본은 영구 보관하지 않는다.
 *
 * <p>순찰은 트랜잭션 밖이고 Job 하나마다 {@link JobDatasetService#deleteDatasetFile} 가 짧은 트랜잭션을 연다.
 * 같은 클래스 안에서 부르면 프록시를 거치지 않아 트랜잭션이 걸리지 않으므로 따로 둔다 (EscrowReconciler 와 같은 이유).
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class JobDatasetCleanup {

    private final JobDatasetService jobDatasetService;

    @Scheduled(fixedDelay = 300_000, initialDelay = 60_000)
    public void sweep() {
        for (Job job : jobDatasetService.findFilesToClean()) {
            try {
                jobDatasetService.deleteDatasetFile(job.getId());
            } catch (RuntimeException e) {
                log.warn("[Dataset] cleanup 실패 jobId={} key={} — 다음 순찰에 재시도",
                        job.getId(), job.getDatasetObjectKey(), e);
            }
        }
    }
}
