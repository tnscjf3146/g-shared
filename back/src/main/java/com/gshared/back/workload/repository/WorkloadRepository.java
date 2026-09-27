package com.gshared.back.workload.repository;

import com.gshared.back.workload.domain.Workload;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface WorkloadRepository extends JpaRepository<Workload, Long> {

    // Job 생성 시 "존재하고 사용 가능한" 메뉴만 통과
    Optional<Workload> findByIdAndEnabledTrue(Long id);

    // 테스트·seed 조회용 (slug 는 unique)
    Optional<Workload> findBySlug(String slug);

    // 메뉴판 조회 — 꺼 둔 항목은 고를 수 없으니 애초에 내보내지 않는다.
    // 정렬을 PK 로 고정하는 것은 새 메뉴가 늘어도 기존 항목의 화면 위치가 흔들리지 않게 하기 위함이다.
    List<Workload> findAllByEnabledTrueOrderByIdAsc();
}
