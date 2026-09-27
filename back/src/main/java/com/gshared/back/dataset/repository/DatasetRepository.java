package com.gshared.back.dataset.repository;

import com.gshared.back.dataset.domain.Dataset;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface DatasetRepository extends JpaRepository<Dataset, Long> {

    // 본인 Dataset 단건 (소유권 확인까지 한 번에 — Job과 같은 패턴)
    Optional<Dataset> findByIdAndOwnerId(Long id, Long ownerId);
}
