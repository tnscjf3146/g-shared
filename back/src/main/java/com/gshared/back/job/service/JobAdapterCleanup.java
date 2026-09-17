package com.gshared.back.job.service;

import com.gshared.back.job.domain.Job;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 입력 어댑터 파일 cleanup 순찰. 끝난 Job(COMPLETED/FAILED/CANCELLED)과 예치 없이 방치된 CREATED Job 의
 * 파일을 지운다 — 입력은 영구 보관하지 않는다. 데이터셋 순찰({@link JobDatasetCleanup})과 같은 구조다.
 *
 * <p>순찰은 트랜잭션 밖이고 Job 하나마다 {@link JobAdapterService#deleteAdapterFile} 가 짧은 트랜잭션을 연다 —
 * 한 파일의 I/O 실패가 나머지를 막지 않는다.
 *
 * <p>데이터셋 순찰과 시각을 어긋나게(initialDelay) 두어 두 순찰이 같은 순간에 스토리지를 두드리지 않게 한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class JobAdapterCleanup {

    private final JobAdapterService jobAdapterService;

    @Scheduled(fixedDelay = 300_000, initialDelay = 120_000)
    public void sweep() {
        for (Job job : jobAdapterService.findFilesToClean()) {
            try {
                jobAdapterService.deleteAdapterFile(job.getId());
            } catch (RuntimeException e) {
                log.warn("[Adapter] cleanup 실패 jobId={} key={} — 다음 순찰에 재시도",
                        job.getId(), job.getAdapterObjectKey(), e);
            }
        }
    }
}
