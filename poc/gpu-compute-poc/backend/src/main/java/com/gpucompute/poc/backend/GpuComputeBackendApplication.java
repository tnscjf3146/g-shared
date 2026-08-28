package com.gpucompute.poc.backend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class GpuComputeBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(GpuComputeBackendApplication.class, args);
    }
}
