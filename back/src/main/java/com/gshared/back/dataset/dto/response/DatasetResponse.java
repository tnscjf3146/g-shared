package com.gshared.back.dataset.dto.response;

import com.gshared.back.dataset.domain.Dataset;

import java.time.LocalDateTime;

public record DatasetResponse(
        Long datasetId,
        Long sizeBytes,
        Integer sampleCount,
        Long totalTokens,
        Long effectiveTokens,
        Double avgSeqLength,
        Integer maxSeqLength,
        String dataHash,
        String storageUri,
        LocalDateTime createdAt
) {
    public static DatasetResponse from(Dataset dataset) {
        return new DatasetResponse(
                dataset.getId(),
                dataset.getSizeBytes(),
                dataset.getSampleCount(),
                dataset.getTotalTokens(),
                dataset.getEffectiveTokens(),
                dataset.getAvgSeqLength(),
                dataset.getMaxSeqLength(),
                dataset.getDataHash(),
                dataset.getStorageUri(),
                dataset.getCreatedAt()
        );
    }
}
