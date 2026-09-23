package com.gshared.back.worker.dto.response;

import com.gshared.back.user.domain.User;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerAvailability;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class WorkerResponseReliabilityTest {
    @Test
    void noExecutionHistoryHasNoSuccessRate() {
        WorkerResponse response = WorkerResponse.from(worker(0, 0), WorkerAvailability.AVAILABLE);

        assertThat(response.completedCount()).isZero();
        assertThat(response.failedCount()).isZero();
        assertThat(response.totalExecutionCount()).isZero();
        assertThat(response.successRate()).isNull();
    }

    @Test
    void successRateIsAZeroToOneRatio() {
        WorkerResponse response = WorkerResponse.from(worker(24, 1), WorkerAvailability.AVAILABLE);

        assertThat(response.completedCount()).isEqualTo(24);
        assertThat(response.failedCount()).isEqualTo(1);
        assertThat(response.totalExecutionCount()).isEqualTo(25);
        assertThat(response.successRate()).isEqualTo(0.96);
    }

    private static Worker worker(long completed, long failed) {
        Worker worker = mock(Worker.class);
        User owner = mock(User.class);
        when(worker.getOwner()).thenReturn(owner);
        when(worker.getCompletedCount()).thenReturn(completed);
        when(worker.getFailedCount()).thenReturn(failed);
        return worker;
    }
}
