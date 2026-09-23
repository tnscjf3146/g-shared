package com.gshared.back.job.controller;

import com.gshared.back.auth.repository.TokenBlacklistRepository;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.job.realtime.JobRealtimeSubscriptionService;
import com.gshared.back.user.domain.Role;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.http.HttpHeaders;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.datasource.url=jdbc:h2:mem:realtime-security;MODE=MySQL;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.datasource.username=sa",
        "spring.datasource.password=",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "spring.flyway.enabled=false",
        "jwt.secret=0123456789012345678901234567890123456789012345678901234567890123",
        "cors.allowed-origins=http://localhost:3000"
})
@AutoConfigureMockMvc
class JobRealtimeSecurityIntegrationTest {
    private static final String URL = "/api/jobs/13/events";

    @Autowired MockMvc mockMvc;
    @Autowired JwtProvider jwtProvider;
    @MockitoBean JobRealtimeSubscriptionService subscriptions;
    @MockitoBean TokenBlacklistRepository tokenBlacklist;
    @MockitoBean StringRedisTemplate redis;
    @MockitoBean ValueOperations<String, String> values;

    private String ownerToken;

    @BeforeEach
    void setUp() {
        ownerToken = jwtProvider.createAccessToken(7L, Role.USER);
        when(redis.hasKey(anyString())).thenReturn(false);
        when(redis.opsForValue()).thenReturn(values);
        when(values.increment(anyString())).thenReturn(1L);
        when(subscriptions.subscribe(7L, 13L)).thenReturn(new SseEmitter(0L));
    }

    @Test
    void bearerTokenCanOpenTheSseStream() throws Exception {
        mockMvc.perform(get(URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .header(HttpHeaders.ACCEPT, "text/event-stream"))
                .andExpect(status().isOk())
                .andExpect(request().asyncStarted())
                .andExpect(header().string(HttpHeaders.CONTENT_TYPE, "text/event-stream"))
                .andExpect(header().string("X-Accel-Buffering", "no"))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, org.hamcrest.Matchers.containsString("no-cache")));

        verify(subscriptions).subscribe(7L, 13L);
    }

    @Test
    void missingTokenIsRejected() throws Exception {
        mockMvc.perform(get(URL).header(HttpHeaders.ACCEPT, "text/event-stream"))
                .andExpect(status().isUnauthorized());

        verify(subscriptions, never()).subscribe(7L, 13L);
    }

    @Test
    void queryTokenDoesNotBypassBearerAuthentication() throws Exception {
        mockMvc.perform(get(URL)
                        .queryParam("token", ownerToken)
                        .header(HttpHeaders.ACCEPT, "text/event-stream"))
                .andExpect(status().isUnauthorized());

        verify(subscriptions, never()).subscribe(7L, 13L);
    }
}
