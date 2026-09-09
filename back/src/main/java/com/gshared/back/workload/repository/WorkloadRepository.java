package com.gshared.back.workload.repository;

import com.gshared.back.workload.domain.Workload;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface WorkloadRepository extends JpaRepository<Workload, String> {

    // Job 생성 시 "존재하고 사용 가능한" 메뉴만 통과
    Optional<Workload> findByIdAndEnabledTrue(String id);
}
