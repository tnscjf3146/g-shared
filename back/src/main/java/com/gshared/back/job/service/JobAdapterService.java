package com.gshared.back.job.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.storage.ObjectStorage;
import com.gshared.back.global.storage.StorageProperties;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.request.AdapterUploadCompleteRequest;
import com.gshared.back.job.dto.response.AdapterUploadCompleteResponse;
import com.gshared.back.job.dto.response.AdapterUploadResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Locale;

/**
 * 입력 LoRA 어댑터 파일의 수명을 Job 에 묶어 관리한다 (lora-adapter-backend-작업 §3·§7).
 *
 * <p>흐름은 데이터셋 원본({@link JobDatasetService})과 같다:
 * <pre>
 *   Job 생성(CREATED) → 업로드 URL 발급 → 프런트가 PUT → 완료 검증 → /fund 허용
 *   → 워커가 execution-context 의 adapter.downloadUrl 로 받아감 → Job 종료 → cleanup 이 삭제
 * </pre>
 *
 * <p>데이터셋과 다른 점은 <b>대조할 등록 메타가 없다</b>는 것이다 — 어댑터는 사용자가 다른 데서 받아 온 파일이라
 * Backend 가 미리 아는 크기·해시가 없다. 그 자리를 형식 검사({@link AdapterTarFormat})가 대신한다:
 * 어댑터가 아닌 파일을 예치 <b>전에</b> 걸러 낸다.
 *
 * <p>"이어서 학습"(parentJobId)은 이 서비스를 쓰지 않는다 — 입력 어댑터가 부모 Job 의 결과물이라 올릴 것이 없다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobAdapterService {

    /** 예치 없이 이만큼 지난 CREATED Job 의 파일은 버려진 견적으로 보고 지운다 (데이터셋과 같은 기준). */
    static final Duration ABANDONED_AFTER = Duration.ofHours(24);
    /** 어댑터 크기 상한 — LoRA 는 보통 수십~수백 MB. 스토리지 전역 상한(10 GiB)보다 좁게 잡는다. */
    static final long MAX_ADAPTER_BYTES = 1024L * 1024 * 1024;

    private final JobRepository jobRepository;
    private final ObjectStorage objectStorage;
    private final StorageProperties storageProperties;

    /**
     * 업로드 URL 발급. 예치 전(CREATED)에만 — 예치 뒤에 바꾸면 워커가 견적과 다른 입력을 받는다.
     * 오브젝트 키는 Job 마다 하나로 고정({@link #adapterObjectKey}) — 다시 올리면 덮어쓴다(봉인 전까지).
     */
    public AdapterUploadResponse createUpload(Long ownerId, Long jobId) {
        Job job = findAdapterJob(ownerId, jobId);
        String objectKey = adapterObjectKey(job.getId());
        ObjectStorage.SignedUrl signed = objectStorage.presignUpload(
                objectKey, "application/octet-stream", storageProperties.uploadTtl());
        return new AdapterUploadResponse(objectKey, signed.url(), signed.expiresAt());
    }

    /**
     * 업로드 완료. 신고값을 믿지 않고 오브젝트가 실제로 있는지, 크기·해시가 맞는지, 어댑터 형식인지 보고 기록한다.
     * 본문(신고값)은 선택이고, 오면 그 값과도 대조한다.
     */
    @Transactional
    public AdapterUploadCompleteResponse completeUpload(Long ownerId, Long jobId, AdapterUploadCompleteRequest request) {
        Job job = findAdapterJob(ownerId, jobId);
        String objectKey = adapterObjectKey(job.getId());
        if (request.objectKey() != null && !request.objectKey().equals(objectKey)) {
            throw new BusinessException(JobErrorCode.ADAPTER_OBJECT_KEY_MISMATCH);   // 남의 경로를 내 파일이라 우기지 못하게
        }

        // 크기 상한은 해시(stat) 전에 본다 — 상한을 넘는 파일을 끝까지 읽고 나서 거절하면 그 I/O 가 낭비고,
        // 유효한 서명 URL 하나로 그걸 반복시킬 수 있다. size() 는 메타데이터만 읽는다.
        long sizeBytes = objectStorage.size(objectKey)
                .orElseThrow(() -> new BusinessException(JobErrorCode.ADAPTER_OBJECT_NOT_FOUND));
        if (sizeBytes > MAX_ADAPTER_BYTES) {
            throw new BusinessException(JobErrorCode.ADAPTER_TOO_LARGE);
        }
        if (sizeBytes == 0) {
            throw new BusinessException(JobErrorCode.ADAPTER_INVALID_FORMAT);
        }
        if (request.sizeBytes() != null && sizeBytes != request.sizeBytes()) {
            log.warn("[Adapter] 업로드 크기 불일치 jobId={} 신고 size={} / 실제 size={}", jobId, request.sizeBytes(), sizeBytes);
            throw new BusinessException(JobErrorCode.ADAPTER_UPLOAD_MISMATCH);
        }

        ObjectStorage.ObjectStat stat = objectStorage.stat(objectKey)
                .orElseThrow(() -> new BusinessException(JobErrorCode.ADAPTER_OBJECT_NOT_FOUND));
        String reported = request.sha256() == null ? null : request.sha256().toLowerCase(Locale.ROOT);
        if (reported != null && !stat.sha256().equals(reported)) {
            log.warn("[Adapter] 업로드 해시 불일치 jobId={} 신고 sha256={} / 실제 sha256={}", jobId, reported, stat.sha256());
            throw new BusinessException(JobErrorCode.ADAPTER_UPLOAD_MISMATCH);
        }
        AdapterTarFormat.validate(objectStorage, objectKey, stat.sizeBytes()).ifPresent(reason -> {
            log.warn("[Adapter] 형식 거절 jobId={} 사유={}", jobId, reason);   // 사용자에게는 고정 문구 — 내부 구조를 흘리지 않는다
            throw new BusinessException(JobErrorCode.ADAPTER_INVALID_FORMAT);
        });

        // 검증이 끝난 입력은 봉인 — 살아 있는 업로드 URL 로 바꿔치기하지 못하게 (데이터셋·결과물과 같은 규칙)
        objectStorage.seal(objectKey);
        job.recordAdapterUpload(objectKey, stat.sizeBytes(), stat.sha256(), LocalDateTime.now());
        return new AdapterUploadCompleteResponse(job.getId(), objectKey, true);
    }

    /** 더는 필요 없는 어댑터 파일을 가진 Job 목록. 순찰({@link JobAdapterCleanup})이 부른다. */
    public List<Job> findFilesToClean() {
        return jobRepository.findAdapterFilesToClean(LocalDateTime.now().minus(ABANDONED_AFTER));
    }

    /** 어댑터 하나 삭제 + 기록. 순찰이 Job 마다 따로 부른다 — 한 파일의 I/O 실패가 나머지를 막지 않게. */
    @Transactional
    public void deleteAdapterFile(Long jobId) {
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || !job.hasAdapterFile()) {
            return;   // 그 사이 지워졌거나 다시 올라온 것 — 이번 순찰은 건너뛴다
        }
        objectStorage.delete(job.getAdapterObjectKey());
        job.markAdapterDeleted(LocalDateTime.now());
        log.info("[Adapter] 입력 파일 삭제 jobId={} status={} key={}", jobId, job.getStatus(), job.getAdapterObjectKey());
    }

    // ── 내부 ─────────────────────────────────────────────────────

    /** 본인의 CREATED Job 이고, 어댑터를 올리기로 한(그리고 올릴 것이 있는) Job 인지. */
    private Job findAdapterJob(Long ownerId, Long jobId) {
        Job job = jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
        if (job.getStatus() != JobStatus.CREATED) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }
        // 이어서 학습은 부모 결과물을 쓰므로 올릴 파일이 없다 — 올리려 하면 주문과 어긋난 것
        if (!job.isAdapterRequired() || job.isContinuedTraining()) {
            throw new BusinessException(JobErrorCode.ADAPTER_NOT_EXPECTED);
        }
        return job;
    }

    /** 어댑터는 Job 마다 이 한 키에만 둔다 — 발급·완료 검증·execution-context·cleanup 이 전부 같은 값을 본다. */
    static String adapterObjectKey(Long jobId) {
        return "jobs/" + jobId + "/input/adapter.tar";
    }
}
