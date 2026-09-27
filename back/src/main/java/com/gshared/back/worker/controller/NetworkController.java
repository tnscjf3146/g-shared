package com.gshared.back.worker.controller;

import com.gshared.back.worker.dto.response.NetworkStatsResponse;
import com.gshared.back.worker.service.NetworkStatsService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 네트워크 지표. 로그인 사용자 누구나 — 자기 것이 아니라 네트워크 전체를 본다. */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/network")
public class NetworkController {

    private final NetworkStatsService networkStatsService;

    @GetMapping("/stats")
    public ResponseEntity<NetworkStatsResponse> getStats() {
        return ResponseEntity.ok(networkStatsService.getStats());
    }
}
