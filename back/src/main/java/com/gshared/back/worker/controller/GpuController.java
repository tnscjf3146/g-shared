package com.gshared.back.worker.controller;

import com.gshared.back.worker.dto.response.GpuResponse;
import com.gshared.back.worker.service.GpuCatalogService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * GPU 등급 조회 창구. 지금 일을 받을 수 있는 그래픽카드 종류를 알려 준다.
 */
@RestController
@RequiredArgsConstructor
public class GpuController {

    private final GpuCatalogService gpuCatalogService;

    @GetMapping("/api/gpus")
    public ResponseEntity<List<GpuResponse>> getGpus() {
        return ResponseEntity.ok(gpuCatalogService.getGpus());
    }
}
