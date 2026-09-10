package com.gshared.back.dataset.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import com.gshared.back.user.domain.User;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 학습 데이터셋 메타데이터(명세서).
 * 원본 파일이 아니라 "이 데이터가 어떤 규모인지"만 보관한다.
 * Scheduler 입력 6개는 Frontend 가 업로드 시 분석해서 보낸다 (원본 분포가 있어야 나오는 값 포함).
 */
@Entity
@Getter
@Table(name = "datasets")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Dataset extends BaseTimeEntity {

    @Id
    @Column(name = "dataset_id")
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "owner_id", nullable = false)
    private User owner;

    // ── Scheduler 입력 6개 (전부 필수) ─────────────────────
    @Column(nullable = false)
    private Long sizeBytes;
    @Column(nullable = false)
    private Integer sampleCount;
    @Column(nullable = false)
    private Long totalTokens;        // 데이터셋에 담긴 토큰
    @Column(nullable = false)
    private Long effectiveTokens;    // GPU가 실제 계산하는 토큰 (billing 기준)
    @Column(nullable = false)
    private Double avgSeqLength;
    @Column(nullable = false)
    private Integer maxSeqLength;

    // ── 저장 위치·검증 (선택) ─────────────────────────────
    @Column(length = 66)
    private String dataHash;
    @Column(length = 512)
    private String storageUri;

    private LocalDateTime uploadedAt;
    private LocalDateTime expiresAt;

    private Dataset(User owner, Long sizeBytes, Integer sampleCount,
                    Long totalTokens, Long effectiveTokens,
                    Double avgSeqLength, Integer maxSeqLength,
                    String dataHash, String storageUri) {
        this.owner = owner;
        this.sizeBytes = sizeBytes;
        this.sampleCount = sampleCount;
        this.totalTokens = totalTokens;
        this.effectiveTokens = effectiveTokens;
        this.avgSeqLength = avgSeqLength;
        this.maxSeqLength = maxSeqLength;
        this.dataHash = dataHash;
        this.storageUri = storageUri;
    }

    public static Dataset create(User owner, Long sizeBytes, Integer sampleCount,
                                 Long totalTokens, Long effectiveTokens,
                                 Double avgSeqLength, Integer maxSeqLength,
                                 String dataHash, String storageUri) {
        return new Dataset(owner, sizeBytes, sampleCount, totalTokens, effectiveTokens,
                avgSeqLength, maxSeqLength, dataHash, storageUri);
    }
}
