package com.gshared.back.dataset.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.dto.request.CreateDatasetRequest;
import com.gshared.back.dataset.dto.response.DatasetResponse;
import com.gshared.back.dataset.exception.DatasetErrorCode;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class DatasetService {

    private final DatasetRepository datasetRepository;
    private final UserRepository userRepository;

    /** Dataset 메타데이터 등록. (원본 파일 업로드가 아니라 분석 결과 저장) */
    @Transactional
    public DatasetResponse create(Long ownerId, CreateDatasetRequest request) {
        User owner = userRepository.findById(ownerId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));

        Dataset dataset = Dataset.create(
                owner,
                request.sizeBytes(), request.sampleCount(),
                request.totalTokens(), request.effectiveTokens(),
                request.avgSeqLength(), request.maxSeqLength(),
                request.dataHash(), request.storageUri()
        );
        datasetRepository.save(dataset);

        return DatasetResponse.from(dataset);
    }

    /** 본인 Dataset 단건 조회. 남의 것은 404. */
    public DatasetResponse getDataset(Long ownerId, Long datasetId) {
        Dataset dataset = datasetRepository.findByIdAndOwnerId(datasetId, ownerId)
                .orElseThrow(() -> new BusinessException(DatasetErrorCode.DATASET_NOT_FOUND));
        return DatasetResponse.from(dataset);
    }
}
