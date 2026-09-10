package com.gshared.back.workload.service;

import com.gshared.back.workload.dto.response.BaseModelResponse;
import com.gshared.back.workload.dto.response.WorkloadResponse;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * 메뉴판 조회. seed 로 관리하는 카탈로그라 쓰기 경로가 없다.
 */
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class CatalogService {

    private final WorkloadRepository workloadRepository;
    private final BaseModelRepository baseModelRepository;

    public List<WorkloadResponse> getWorkloads() {
        return workloadRepository.findAllByEnabledTrueOrderByIdAsc().stream()
                .map(WorkloadResponse::from)
                .toList();
    }

    public List<BaseModelResponse> getBaseModels() {
        return baseModelRepository.findAllEnabledWithWorkload().stream()
                .map(BaseModelResponse::from)
                .toList();
    }
}
