package com.gpucompute.poc.backend.job;

public class JobConflictException extends RuntimeException {
    public JobConflictException(String message) {
        super(message);
    }
}
