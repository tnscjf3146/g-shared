package com.gshared.back.job.realtime;

import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

class JobRealtimeDisconnectTest {
    @Test
    void completionRemovesOnlyItsSubscription() {
        JobRealtimeEventStream stream = new JobRealtimeEventStream();
        SseEmitter first = stream.subscribe(13L);
        SseEmitter second = stream.subscribe(13L);

        callback(first, "completionCallback").run();

        assertThat(emitters(stream).get(13L)).containsExactly(second);
    }

    @Test
    void timeoutRemovesTheSubscription() {
        JobRealtimeEventStream stream = new JobRealtimeEventStream();
        SseEmitter emitter = stream.subscribe(13L);

        callback(emitter, "timeoutCallback").run();

        assertThat(emitters(stream)).doesNotContainKey(13L);
    }

    @Test
    void errorRemovesTheSubscription() {
        JobRealtimeEventStream stream = new JobRealtimeEventStream();
        SseEmitter emitter = stream.subscribe(13L);

        errorCallback(emitter).accept(new IOException("closed"));

        assertThat(emitters(stream)).doesNotContainKey(13L);
    }

    @Test
    void duplicateCleanupIsSafe() {
        JobRealtimeEventStream stream = new JobRealtimeEventStream();
        SseEmitter emitter = stream.subscribe(13L);

        callback(emitter, "completionCallback").run();
        callback(emitter, "completionCallback").run();
        errorCallback(emitter).accept(new IOException("closed"));

        assertThat(emitters(stream)).doesNotContainKey(13L);
    }

    @SuppressWarnings("unchecked")
    @Test
    void disconnectedBrowserIsRemovedWithoutAffectingPublisher() throws Exception {
        JobRealtimeEventStream stream = new JobRealtimeEventStream();
        SseEmitter disconnected = mock(SseEmitter.class);
        doThrow(new IOException("closed")).when(disconnected).send(any(SseEmitter.SseEventBuilder.class));
        ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> emitters = emitters(stream);
        emitters.put(13L, new CopyOnWriteArrayList<>(java.util.List.of(disconnected)));

        assertThatCode(() -> stream.publish(JobRealtimeEvent.progress(13L, 62.0, 31, 50)))
                .doesNotThrowAnyException();
        assertThatCode(() -> stream.publish(JobRealtimeEvent.progress(13L, 63.0, 32, 50)))
                .doesNotThrowAnyException();

        verify(disconnected, times(1)).send(any(SseEmitter.SseEventBuilder.class));
    }

    @SuppressWarnings("unchecked")
    private static ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> emitters(
            JobRealtimeEventStream stream) {
        return (ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>>)
                ReflectionTestUtils.getField(stream, "emitters");
    }

    private static Runnable callback(SseEmitter emitter, String field) {
        return (Runnable) ReflectionTestUtils.getField(emitter, field);
    }

    @SuppressWarnings("unchecked")
    private static Consumer<Throwable> errorCallback(SseEmitter emitter) {
        return (Consumer<Throwable>) ReflectionTestUtils.getField(emitter, "errorCallback");
    }
}
