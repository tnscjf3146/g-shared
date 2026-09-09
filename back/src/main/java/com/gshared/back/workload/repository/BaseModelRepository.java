package com.gshared.back.workload.repository;

import com.gshared.back.workload.domain.BaseModel;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface BaseModelRepository extends JpaRepository<BaseModel, Long> {

    // Job 생성 시 "존재하고 사용 가능한" 모델만 통과
    Optional<BaseModel> findByIdAndEnabledTrue(Long id);

    // 테스트·seed 조회용 (slug 는 unique)
    Optional<BaseModel> findBySlug(String slug);
}
