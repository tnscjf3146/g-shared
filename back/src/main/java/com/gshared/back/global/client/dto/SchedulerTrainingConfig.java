package com.gshared.back.global.client.dto;

/** 학습 설정 4종 — recommend 요청에 싣고, AUTO 확정(training-config) 응답으로도 받는다. */
public record SchedulerTrainingConfig(
        Integer epochs,
        Integer batchSize,
        Double learningRate,
        Integer maxLength
) {
}
