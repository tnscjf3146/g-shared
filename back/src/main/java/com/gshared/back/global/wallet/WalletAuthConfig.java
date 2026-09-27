package com.gshared.back.global.wallet;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(WalletAuthProperties.class)
public class WalletAuthConfig {
}
