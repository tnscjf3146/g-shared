package com.gshared.back.workload.controller;

import com.gshared.back.workload.dto.response.BaseModelResponse;
import com.gshared.back.workload.dto.response.WorkloadResponse;
import com.gshared.back.workload.service.CatalogService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 카탈로그(메뉴판) 조회 창구.
 *
 * <p>경로가 {@code /api/catalog/...} 가 아니라 {@code /api/workloads}·{@code /api/base-models}
 * 인 것은 각각이 독립된 자원이기 때문이다. 화면은 한 번에 다 받아 가지만, 그건 화면 사정이지
 * 서버가 묶어 줄 이유는 아니다.
 */
@RestController
@RequiredArgsConstructor
public class CatalogController {

    private final CatalogService catalogService;

    @GetMapping("/api/workloads")
    public ResponseEntity<List<WorkloadResponse>> getWorkloads() {
        return ResponseEntity.ok(catalogService.getWorkloads());
    }

    @GetMapping("/api/base-models")
    public ResponseEntity<List<BaseModelResponse>> getBaseModels() {
        return ResponseEntity.ok(catalogService.getBaseModels());
    }
}
