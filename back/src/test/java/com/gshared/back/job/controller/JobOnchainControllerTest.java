package com.gshared.back.job.controller;

import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.job.dto.response.JobOnchainResponse;
import com.gshared.back.job.service.JobAdapterService;
import com.gshared.back.job.service.JobDatasetService;
import com.gshared.back.job.service.JobEstimateService;
import com.gshared.back.job.service.JobOnchainVerificationService;
import com.gshared.back.job.service.JobResultVerificationService;
import com.gshared.back.job.service.JobService;
import com.gshared.back.user.domain.Role;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobOnchainControllerTest {
    @Mock JobService jobs;
    @Mock JobResultVerificationService resultVerification;
    @Mock JobOnchainVerificationService onchainVerification;
    @Mock JobEstimateService estimates;
    @Mock JobDatasetService datasets;
    @Mock JobAdapterService adapters;

    @Test
    void authenticatedOwnerIdentityIsForwardedToTheOnchainLookup() {
        JobController controller = new JobController(jobs, resultVerification, onchainVerification,
                estimates, datasets, adapters);
        CustomUserDetails user = CustomUserDetails.of(7L, Role.USER);
        JobOnchainResponse expected = JobOnchainResponse.unavailable(13L, null, "ESCROW_JOB_ID_MISSING");
        when(onchainVerification.get(7L, 13L)).thenReturn(expected);

        var response = controller.getOnchain(13L, user);

        assertThat(response.getBody()).isSameAs(expected);
        verify(onchainVerification).get(7L, 13L);
    }
}
