package com.gshared.back.workload.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 베이스 모델 메뉴판. 이미지(Workload) 1 : 모델 N.
 * seed 카탈로그 — 조회 전용.
 */
@Entity
@Getter
@Table(name = "base_models")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class BaseModel extends BaseTimeEntity {

    @Id
    @Column(name = "model_id", length = 64)
    private String id;               // "qwen2.5-7b" — Scheduler modelId

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workload_id", nullable = false)
    private Workload workload;

    @Column(nullable = false, length = 128)
    private String displayName;

    private Long paramCount;

    @Column(length = 66)
    private String weightsHash;

    @Column(length = 512)
    private String sourceUri;

    @Column(nullable = false)
    private Boolean enabled;
}
