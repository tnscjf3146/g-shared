package com.gshared.back.job.realtime;

import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalApplicationListener;
import org.springframework.transaction.event.TransactionalEventListener;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class JobRealtimeAfterCommitTest {
    @Test
    void productionListenerIsAfterCommitOnly() throws Exception {
        Method publish = JobRealtimeEventStream.class.getMethod("publish", JobRealtimeEvent.class);
        TransactionalEventListener annotation = publish.getAnnotation(TransactionalEventListener.class);

        assertThat(annotation).isNotNull();
        assertThat(annotation.phase()).isEqualTo(TransactionPhase.AFTER_COMMIT);
        assertThat(annotation.fallbackExecution()).isFalse();
    }

    @Test
    void committedEventIsDeliveredOnceAndRolledBackEventIsNotDelivered() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext(TestConfig.class)) {
            List<JobRealtimeEvent> events = new ArrayList<>();
            context.addApplicationListener(
                    TransactionalApplicationListener.<JobRealtimeEvent>forPayload(
                            TransactionPhase.AFTER_COMMIT, events::add));
            TransactionTemplate transaction = new TransactionTemplate(context.getBean(PlatformTransactionManager.class));

            transaction.executeWithoutResult(ignored ->
                    context.publishEvent(JobRealtimeEvent.progress(13L, 62.0, 31, 50)));
            assertThat(events).hasSize(1);

            transaction.executeWithoutResult(status -> {
                context.publishEvent(JobRealtimeEvent.progress(13L, 63.0, 32, 50));
                status.setRollbackOnly();
            });
            assertThat(events).hasSize(1);
        }
    }

    @Configuration
    public static class TestConfig {
        @Bean
        PlatformTransactionManager transactionManager() {
            return new StubTransactionManager();
        }
    }

    static class StubTransactionManager extends AbstractPlatformTransactionManager {
        @Override
        protected Object doGetTransaction() {
            return new Object();
        }

        @Override
        protected void doBegin(Object transaction, TransactionDefinition definition) {
        }

        @Override
        protected void doCommit(DefaultTransactionStatus status) {
        }

        @Override
        protected void doRollback(DefaultTransactionStatus status) {
        }
    }
}
