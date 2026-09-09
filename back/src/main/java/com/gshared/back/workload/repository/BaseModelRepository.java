package com.gshared.back.workload.repository;

import com.gshared.back.workload.domain.BaseModel;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface BaseModelRepository extends JpaRepository<BaseModel, String> {

    // Job 생성 시 "존재하고 사용 가능한" 모델만 통과
    Optional<BaseModel> findByIdAndEnabledTrue(String id);
}
