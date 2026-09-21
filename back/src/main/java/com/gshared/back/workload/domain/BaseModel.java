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

    /**
     * 이 모델을 학습하는 데 필요한 VRAM 하한(MiB). 실측으로 확정되기 전에는 NULL 이고,
     * 그동안은 workload.minVramMb 가 대신한다 — {@link #requiredVramMb()} (V21).
     */
    private Integer minVramMb;

    @Column(length = 66)
    private String weightsHash;

    @Column(length = 512)
    private String sourceUri;

    @Column(nullable = false)
    private Boolean enabled;

    /**
     * Scheduler 에 실어 보내고 gpu-options 에도 그대로 내보내는 VRAM 하한.
     * 모델 값이 있으면 모델 값이 이긴다(더 정확한 값이라서). 없으면 워크로드 값 —
     * 그래서 min_vram_mb 를 채우기 전에는 동작이 바뀌지 않는다.
     * 화면 표시(requiredVramMb)와 Scheduler 판정(requirements.minVramMb)이 같은 값을 써야 하므로 한 곳에 둔다.
     */
    public int requiredVramMb() {
        return minVramMb != null ? minVramMb : workload.getMinVramMb();
    }
}
