package com.gshared.back.workload.repository;

import com.gshared.back.workload.domain.Workload;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface WorkloadRepository extends JpaRepository<Workload, Long> {

    // Job 생성 시 "존재하고 사용 가능한" 메뉴만 통과
    Optional<Workload> findByIdAndEnabledTrue(Long id);

    // 테스트·seed 조회용 (slug 는 unique)
    Optional<Workload> findBySlug(String slug);
}
