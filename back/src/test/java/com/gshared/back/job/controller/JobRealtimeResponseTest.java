package com.gshared.back.job.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.job.realtime.JobRealtimeSubscriptionService;
import com.gshared.back.job.service.JobAdapterService;
import com.gshared.back.job.service.JobDatasetService;
import com.gshared.back.job.service.JobEstimateService;
import com.gshared.back.job.service.JobOnchainVerificationService;
import com.gshared.back.job.service.JobResultVerificationService;
import com.gshared.back.job.service.JobService;
import com.gshared.back.user.domain.Role;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobRealtimeResponseTest {
    @Mock JobService jobs;
    @Mock JobResultVerificationService resultVerification;
    @Mock JobOnchainVerificationService onchainVerification;
    @Mock JobEstimateService estimates;
    @Mock JobDatasetService datasets;
    @Mock JobAdapterService adapters;
    @Mock JobRealtimeSubscriptionService realtimeSubscriptions;
    @InjectMocks JobController controller;

    @Test
    void sseResponseDisablesProxyBufferingAndCaching() {
        SseEmitter emitter = new SseEmitter(0L);
        when(realtimeSubscriptions.subscribe(7L, 13L)).thenReturn(emitter);

        Object response = controller.subscribeToJobEvents(
                13L, CustomUserDetails.of(7L, Role.USER));

        assertThat(response).isInstanceOfSatisfying(ResponseEntity.class, entity -> {
            assertThat(entity.getStatusCode().value()).isEqualTo(200);
            assertThat(entity.getHeaders().getContentType()).isEqualTo(MediaType.TEXT_EVENT_STREAM);
            assertThat(entity.getHeaders().getFirst("X-Accel-Buffering")).isEqualTo("no");
            assertThat(entity.getHeaders().getCacheControl()).contains("no-cache");
            assertThat(entity.getBody()).isSameAs(emitter);
        });
    }
}
