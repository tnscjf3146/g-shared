package com.gshared.back.global.client;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/** application.yml 의 scheduler.* 바인딩 (JwtProperties 와 같은 패턴). */
@ConfigurationProperties(prefix = "scheduler")
public record SchedulerProperties(
        String baseUrl,
        Duration connectTimeout,
        Duration readTimeout
) {
}
