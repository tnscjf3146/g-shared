package com.gshared.back.job.realtime;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobRealtimeSubscriptionService {
    private final JobRepository jobRepository;
    private final JobRealtimeEventStream eventStream;

    public SseEmitter subscribe(Long ownerId, Long jobId) {
        jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
        return eventStream.subscribe(jobId);
    }
}
