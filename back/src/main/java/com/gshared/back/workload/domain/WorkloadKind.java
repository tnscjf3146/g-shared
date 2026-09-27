package com.gshared.back.workload.domain;

/**
 * 워크로드 유형. 사용자가 무엇을 시키는 작업인지 — 화면의 1단계 선택지다.
 * 유형에 따라 뒤 단계에서 물어보는 것이 달라진다(학습 수준·데이터셋의 의미).
 */
public enum WorkloadKind {
    TRAIN_LORA,   // LoRA 어댑터 학습
    INFERENCE,    // 추론
    EVALUATION    // 평가
}
