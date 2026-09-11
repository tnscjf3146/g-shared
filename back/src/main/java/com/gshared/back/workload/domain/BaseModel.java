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
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "model_id")
    private Long id;                 // Scheduler modelId (정수 PK — V8)

    @Column(nullable = false, unique = true, length = 64)
    private String slug;             // "qwen2.5-7b" — 사람용 식별자

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workload_id", nullable = false)
    private Workload workload;

    @Column(nullable = false, length = 128)
    private String displayName;

    @Column(length = 255)
    private String modelRef;         // Hugging Face 저장소 참조 "Qwen/Qwen2.5-7B" — Worker 가 내려받는 대상 (V10)

    @Column(length = 64)
    private String modelRevision;    // 고정 커밋 SHA. 없으면 Worker 가 기본 브랜치를 받는다 — 재현성이 떨어지므로 채울 것

    private Long paramCount;

    @Column(length = 66)
    private String weightsHash;

    @Column(length = 512)
    private String sourceUri;

    @Column(nullable = false)
    private Boolean enabled;
}
