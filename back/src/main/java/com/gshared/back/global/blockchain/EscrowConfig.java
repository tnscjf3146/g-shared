package com.gshared.back.global.blockchain;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(EscrowProperties.class)
public class EscrowConfig {
}
