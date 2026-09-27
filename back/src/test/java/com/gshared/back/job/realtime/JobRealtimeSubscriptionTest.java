package com.gshared.back.job.realtime;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.repository.JobRepository;
import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class JobRealtimeSubscriptionTest {
    @Test
    void subscriptionUsesAnUnlimitedApplicationTimeout() {
        assertThat(new JobRealtimeEventStream().subscribe(13L).getTimeout()).isZero();
    }

    @Test
    void ownerCanSubscribeToOwnJob() {
        JobRepository jobs = mock(JobRepository.class);
        JobRealtimeEventStream stream = mock(JobRealtimeEventStream.class);
        SseEmitter emitter = mock(SseEmitter.class);
        when(jobs.findByIdAndOwnerId(13L, 7L)).thenReturn(Optional.of(mock(Job.class)));
        when(stream.subscribe(13L)).thenReturn(emitter);

        JobRealtimeSubscriptionService service = new JobRealtimeSubscriptionService(jobs, stream);

        org.assertj.core.api.Assertions.assertThat(service.subscribe(7L, 13L)).isSameAs(emitter);
        verify(stream).subscribe(13L);
    }

    @Test
    void nonOwnerCannotSubscribe() {
        JobRepository jobs = mock(JobRepository.class);
        JobRealtimeEventStream stream = mock(JobRealtimeEventStream.class);
        when(jobs.findByIdAndOwnerId(13L, 7L)).thenReturn(Optional.empty());

        JobRealtimeSubscriptionService service = new JobRealtimeSubscriptionService(jobs, stream);

        assertThatThrownBy(() -> service.subscribe(7L, 13L)).isInstanceOf(BusinessException.class);
        verifyNoInteractions(stream);
    }
}
