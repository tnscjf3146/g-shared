package com.gshared.back.workload.repository;

import com.gshared.back.workload.domain.BaseModel;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;
import java.util.Optional;

public interface BaseModelRepository extends JpaRepository<BaseModel, Long> {

    // Job 생성 시 "존재하고 사용 가능한" 모델만 통과
    Optional<BaseModel> findByIdAndEnabledTrue(Long id);

    // 테스트·seed 조회용 (slug 는 unique)
    Optional<BaseModel> findBySlug(String slug);

    /**
     * 메뉴판 조회. workload 를 함께 읽는 이유는 응답에 그 id 가 들어가기 때문이다 —
     * LAZY 로 두면 모델 수만큼 추가 쿼리가 나간다(N+1).
     */
    @Query("""
            SELECT m FROM BaseModel m
              JOIN FETCH m.workload
             WHERE m.enabled = true
             ORDER BY m.id ASC
            """)
    List<BaseModel> findAllEnabledWithWorkload();
}
