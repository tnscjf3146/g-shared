package com.gshared.back.dataset.controller;

import com.gshared.back.dataset.dto.request.CreateDatasetRequest;
import com.gshared.back.dataset.dto.response.DatasetResponse;
import com.gshared.back.dataset.service.DatasetService;
import com.gshared.back.global.security.userdetails.CustomUserDetails;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/datasets")
public class DatasetController {

    private final DatasetService datasetService;

    @PostMapping
    public ResponseEntity<DatasetResponse> create(
            @Valid @RequestBody CreateDatasetRequest request,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        DatasetResponse response = datasetService.create(userDetails.getId(), request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    @GetMapping("/{datasetId}")
    public ResponseEntity<DatasetResponse> getDataset(
            @PathVariable Long datasetId,
            @AuthenticationPrincipal CustomUserDetails userDetails
    ) {
        return ResponseEntity.ok(datasetService.getDataset(userDetails.getId(), datasetId));
    }
}
