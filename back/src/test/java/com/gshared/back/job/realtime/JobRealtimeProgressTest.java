package com.gshared.back.job.realtime;

import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.job.service.JobService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobRealtimeProgressTest {
    @Mock JobRepository jobs;
    @Mock ApplicationEventPublisher events;
    @InjectMocks JobService service;

    @Test
    void successfulProgressUpdatePublishesOneDomainEvent() {
        when(jobs.updateProgressIfRunning(13L, "worker", 62.0, 31, 50, 1, 2, 9)).thenReturn(1);

        assertThat(service.updateProgress("worker", 13L, 62.0, 31, 50, 1, 2, 9)).isTrue();

        ArgumentCaptor<Object> published = ArgumentCaptor.forClass(Object.class);
        verify(events).publishEvent(published.capture());
        org.assertj.core.api.Assertions.assertThat(published.getValue())
                .isInstanceOfSatisfying(JobRealtimeEvent.class, event -> {
                    assertThat(event.type()).isEqualTo("JOB_PROGRESS");
                    assertThat(event.jobId()).isEqualTo(13L);
                    assertThat(event.status()).isEqualTo("RUNNING");
                    assertThat(event.progress()).isEqualTo(62.0);
                    assertThat(event.currentStep()).isEqualTo(31);
                    assertThat(event.totalSteps()).isEqualTo(50);
                    assertThat(event.updatedAt()).isNotNull();
                });
    }

    @Test
    void ignoredProgressUpdateDoesNotPublish() {
        when(jobs.updateProgressIfRunning(13L, "worker", 62.0, 31, 50, 1, 2, 9)).thenReturn(0);

        assertThat(service.updateProgress("worker", 13L, 62.0, 31, 50, 1, 2, 9)).isFalse();

        verify(events, never()).publishEvent(org.mockito.ArgumentMatchers.any(Object.class));
    }
}
