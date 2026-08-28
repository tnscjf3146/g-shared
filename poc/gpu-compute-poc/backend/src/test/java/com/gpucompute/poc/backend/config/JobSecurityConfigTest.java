package com.gpucompute.poc.backend.config;

import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.web.socket.WebSocketHandler;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class JobSecurityConfigTest {
    @Test
    void jobApiRejectsMissingTokenAndAcceptsOperatorToken() throws Exception {
        JobSecurityConfig security = new JobSecurityConfig(
            true, "operator-secret", "worker-001=worker-secret"
        );
        MockHttpServletRequest denied = new MockHttpServletRequest("POST", "/api/jobs");
        MockHttpServletResponse deniedResponse = new MockHttpServletResponse();
        security.doFilter(denied, deniedResponse, (request, response) -> {});
        assertThat(deniedResponse.getStatus()).isEqualTo(401);

        MockHttpServletRequest accepted = new MockHttpServletRequest("POST", "/api/jobs");
        accepted.addHeader(HttpHeaders.AUTHORIZATION, "Bearer operator-secret");
        MockHttpServletResponse acceptedResponse = new MockHttpServletResponse();
        boolean[] invoked = {false};
        security.doFilter(accepted, acceptedResponse, (request, response) -> invoked[0] = true);
        assertThat(invoked[0]).isTrue();
    }

    @Test
    void websocketTokenBindsExactlyOneWorkerIdentity() {
        JobSecurityConfig security = new JobSecurityConfig(
            true, "operator-secret", "worker-001=worker-secret"
        );
        ServerHttpRequest request = mock(ServerHttpRequest.class);
        HttpHeaders headers = new HttpHeaders();
        headers.setBearerAuth("worker-secret");
        when(request.getHeaders()).thenReturn(headers);
        Map<String, Object> attributes = new HashMap<>();

        boolean accepted = security.beforeHandshake(
            request,
            mock(ServerHttpResponse.class),
            mock(WebSocketHandler.class),
            attributes
        );

        assertThat(accepted).isTrue();
        assertThat(attributes).containsEntry(
            JobSecurityConfig.AUTHENTICATED_WORKER_ID,
            "worker-001"
        );
    }

    @Test
    void commaSeparatedCredentialsAuthenticateTwoDifferentWorkers() {
        JobSecurityConfig security = new JobSecurityConfig(
            true, "operator-secret",
            "worker-001=worker-one-token,worker-002=worker-two-token"
        );

        assertThat(authenticatedWorker(security, "worker-one-token"))
            .isEqualTo("worker-001");
        assertThat(authenticatedWorker(security, "worker-two-token"))
            .isEqualTo("worker-002");
    }

    private static String authenticatedWorker(JobSecurityConfig security, String token) {
        ServerHttpRequest request = mock(ServerHttpRequest.class);
        HttpHeaders headers = new HttpHeaders();
        headers.setBearerAuth(token);
        when(request.getHeaders()).thenReturn(headers);
        Map<String, Object> attributes = new HashMap<>();
        boolean accepted = security.beforeHandshake(
            request, mock(ServerHttpResponse.class), mock(WebSocketHandler.class), attributes
        );
        assertThat(accepted).isTrue();
        return (String) attributes.get(JobSecurityConfig.AUTHENTICATED_WORKER_ID);
    }
}
