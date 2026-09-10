package com.gshared.back.workload.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 학습 방법 메뉴판 (도커 이미지 단위).
 * 관리자가 seed 로 넣는 카탈로그 — 사용자 등록 API 없음, 조회 전용.
 */
@Entity
@Getter
@Table(name = "workloads")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Workload extends BaseTimeEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "workload_id")
    private Long id;                 // Scheduler dockerImageId (정수 PK — V8)

    @Column(nullable = false, unique = true, length = 32)
    private String slug;             // "qlora-sft-v1" — 사람용 식별자 (이름 변경에도 FK 불변)

    @Column(nullable = false, length = 64)
    private String name;

    @Column(nullable = false, length = 71)
    private String imageDigest;      // Worker 에 실제로 보낼 이미지 지문 (sha256:...)

    @Column(nullable = false)
    private Integer minVramMb;       // Scheduler 에 값만 전달 — Backend 가 직접 거르지 않는다

    @Column(columnDefinition = "JSON")
    private String defaultConfig;    // AUTO 기본값 (JSON)

    @Column(columnDefinition = "JSON")
    private String limits;

    @Column(nullable = false)
    private Boolean enabled;
}
