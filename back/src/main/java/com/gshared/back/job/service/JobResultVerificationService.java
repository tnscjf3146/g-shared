package com.gshared.back.job.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.storage.ObjectStorage;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.response.JobResultVerificationResponse;
import com.gshared.back.job.dto.response.JobResultVerificationResponse.Adapter;
import com.gshared.back.job.dto.response.JobResultVerificationResponse.Integrity;
import com.gshared.back.job.dto.response.JobResultVerificationResponse.LossPoint;
import com.gshared.back.job.dto.response.JobResultVerificationResponse.Training;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * 학습 결과 검증 조회 (Training_Result_Verification_API_Guide).
 *
 * <p>Runtime 이 결과 tar 에 함께 넣은 {@code training_metrics.json} 만 골라 읽어 그대로 전달한다.
 * <b>archive 전체를 extract 하지 않고</b> {@link ObjectStorage#readRange} 로 tar 헤더를 띄엄띄엄 훑어
 * 그 entry 하나만 읽는다(가이드 §6) — safetensors 는 서버에 로드하지 않는다(§17).
 *
 * <p>검증 데이터가 없는 결과(legacy·metrics 누락·JSON 손상)는 에러가 아니라
 * {@code available=false + reason} — 화면이 이걸 "작업 실패"로 그리면 안 되기 때문이다(§5·§13).
 */
// 의도적으로 @Transactional 없음 — DB 조회는 findByIdAndOwnerId 한 번뿐이고,
// tar 훑기(파일 I/O 최대 수십 회)를 DB 커넥션을 잡은 채 하지 않기 위해서다.
@Slf4j
@Service
@RequiredArgsConstructor
public class JobResultVerificationService {

    static final String METRICS_FILE = "training_metrics.json";
    /** metrics 파일 크기 상한(§7). 실제 payload 는 수십 KB — 1 MiB 를 넘으면 조작으로 본다. */
    static final int MAX_METRICS_BYTES = 1024 * 1024;
    static final int MAX_LOSS_POINTS = 200;
    static final int MAX_LAYERS = 256;
    static final int MAX_MODULES_PER_LAYER = 32;
    /** tar 훑기 상한 = readRange(파일 open) 횟수 상한. 결과 tar 는 파일 서너 개라 64 면 넉넉하다. */
    static final int MAX_TAR_ENTRIES = 64;
    /** GNU longname('L') 본문 상한 — 경로 하나 크기면 충분하다. */
    static final int MAX_LONGNAME_BYTES = 4096;

    private static final int TAR_BLOCK = 512;

    private final JobRepository jobRepository;
    private final ObjectStorage objectStorage;
    private final ObjectMapper mapper = new ObjectMapper();

    /** 소유자 확인은 상세 조회와 동일(findByIdAndOwnerId) — jobId 만으로 남의 메트릭을 볼 수 없다(§9). */
    public JobResultVerificationResponse getVerification(Long ownerId, Long jobId) {
        Job job = jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
        if (job.getStatus() != JobStatus.COMPLETED) {
            return JobResultVerificationResponse.unavailable("RESULT_NOT_READY");
        }
        if (job.getResultObjectKey() == null) {
            return JobResultVerificationResponse.unavailable("LEGACY_RESULT");
        }
        if (objectStorage.size(job.getResultObjectKey()).isEmpty()) {
            // metrics 를 안 넣은 legacy(정상)와 결과 파일 자체가 사라진 운영 이상은 다른 사건이다
            log.warn("[VERIFY] 결과 오브젝트가 스토리지에 없음 jobId={} key={}", jobId, job.getResultObjectKey());
            return JobResultVerificationResponse.unavailable("RESULT_OBJECT_MISSING");
        }
        byte[] metrics;
        try {
            Optional<byte[]> found = findTarEntry(
                    (offset, length) -> objectStorage.readRange(job.getResultObjectKey(), offset, length),
                    METRICS_FILE, MAX_METRICS_BYTES);
            if (found.isEmpty()) {
                return JobResultVerificationResponse.unavailable("METRICS_NOT_AVAILABLE");
            }
            metrics = found.get();
        } catch (MalformedArchiveException e) {
            log.warn("[VERIFY] 결과 tar 훑기 실패 jobId={} {}", jobId, e.getMessage());
            return JobResultVerificationResponse.unavailable("VERIFICATION_PARSE_FAILED");
        }
        try {
            return parse(metrics, job);
        } catch (Exception e) {
            // 손상 JSON 은 500 으로 숨기지 않는다 — 로그에 원인, 화면에는 중립 상태(§13)
            log.warn("[VERIFY] training_metrics.json 파싱 실패 jobId={}", jobId, e);
            return JobResultVerificationResponse.unavailable("VERIFICATION_PARSE_FAILED");
        }
    }

    // ── metrics JSON → DTO ─────────────────────────────────────

    private JobResultVerificationResponse parse(byte[] metricsJson, Job job) {
        JsonNode root = mapper.readTree(metricsJson);

        List<LossPoint> loss = readLossHistory(root.path("lossHistory").isArray()
                ? root.path("lossHistory") : root.path("loss_history"));
        // 요약 값은 Runtime 이 준 것을 우선하고, 없으면 lossHistory·Job 기록에서 채운다 —
        // Runtime 쪽 키 확정 전에도(§3 가정) 화면이 비지 않게. 그래도 없으면 null(화면이 빈 칸 처리).
        Training training = new Training(
                intOrNull(root, "sampleCount", "sample_count"),
                firstNonNull(intOrNull(root, "totalSteps", "total_steps"), loss.isEmpty() ? null : loss.size()),
                firstNonNull(doubleOrNull(root, "finalLoss", "final_loss"),
                        loss.isEmpty() ? null : loss.get(loss.size() - 1).loss()),
                firstNonNull(doubleOrNull(root, "actualRuntimeSec", "actual_runtime_sec"), job.getActualRuntimeSec()),
                loss);

        JsonNode v = root.path("verification");
        Adapter adapter = v.isObject()
                ? new Adapter(
                        intOrNull(v, "tensorCount", "tensor_count"),
                        intOrNull(v, "changedTensorCount", "changed_tensor_count"),
                        intOrNull(v, "nonZeroTensorCount", "non_zero_tensor_count"),
                        intOrNull(v, "nanCount", "nan_count"),
                        intOrNull(v, "infCount", "inf_count"),
                        v.path("valid").isBoolean() ? v.path("valid").booleanValue() : null,
                        readHeatmap(v.path("layerModuleDeltaMagnitude").isObject()
                                ? v.path("layerModuleDeltaMagnitude") : v.path("layer_module_delta_magnitude")))
                : null;

        // hash 는 재계산하지 않는다 — §18 업로드 검증을 거쳐 저장된 값이 정본(§11)
        Integrity integrity = job.getResultHash() == null ? null : new Integrity(job.getResultHash());
        return new JobResultVerificationResponse(true, null, training, adapter, integrity);
    }

    /** 상한(200)을 넘으면 자르지 않고 균등 샘플링한다 — 곡선 모양과 마지막 점을 보존해야 한다. */
    private static List<LossPoint> readLossHistory(JsonNode array) {
        if (!array.isArray() || array.isEmpty()) {
            return List.of();
        }
        int n = array.size();
        List<LossPoint> out = new ArrayList<>(Math.min(n, MAX_LOSS_POINTS));
        if (n <= MAX_LOSS_POINTS) {
            for (JsonNode p : array) {
                addLossPoint(out, p);
            }
            return out;
        }
        for (int i = 0; i < MAX_LOSS_POINTS; i++) {
            // double 산술 — float 는 n 이 수만이면 ULP 오차로 인덱스가 n 을 넘을 수 있다
            addLossPoint(out, array.get((int) Math.round((double) i * (n - 1) / (MAX_LOSS_POINTS - 1))));
        }
        return out;
    }

    /** 숫자가 아닌 step/loss 는 조용히 0 으로 바꾸지 않고 그 점만 버린다 — 차트에 (0, 0.0) 이 섞이지 않게. */
    private static void addLossPoint(List<LossPoint> out, JsonNode p) {
        if (p != null && p.path("step").isNumber() && p.path("loss").isNumber()) {
            out.add(new LossPoint(p.path("step").asInt(), p.path("loss").asDouble()));
        }
    }

    private static Map<String, Map<String, Double>> readHeatmap(JsonNode node) {
        if (!node.isObject()) {
            return Map.of();
        }
        Map<String, Map<String, Double>> out = new LinkedHashMap<>();
        var layers = node.propertyNames().iterator();
        while (layers.hasNext() && out.size() < MAX_LAYERS) {
            String layer = layers.next();
            JsonNode modules = node.path(layer);
            if (!modules.isObject()) {
                continue;
            }
            Map<String, Double> row = new LinkedHashMap<>();
            var names = modules.propertyNames().iterator();
            while (names.hasNext() && row.size() < MAX_MODULES_PER_LAYER) {
                String module = names.next();
                JsonNode value = modules.path(module);
                if (value.isNumber()) {
                    row.put(module, value.asDouble());
                }
            }
            out.put(layer, row);
        }
        return out;
    }

    private static Integer intOrNull(JsonNode node, String camel, String snake) {
        JsonNode v = node.path(camel).isNumber() ? node.path(camel) : node.path(snake);
        return v.isNumber() ? v.asInt() : null;
    }

    private static Double doubleOrNull(JsonNode node, String camel, String snake) {
        JsonNode v = node.path(camel).isNumber() ? node.path(camel) : node.path(snake);
        return v.isNumber() ? v.asDouble() : null;
    }

    private static <T> T firstNonNull(T a, T b) {
        return a != null ? a : b;
    }

    // ── tar 훑기 — extract 없이 헤더만 따라가며 entry 하나를 찾는다 ──

    /** 스토리지 종류와 무관하게 파서를 테스트하기 위한 창구 — 프로덕션은 {@code objectStorage::readRange}. */
    @FunctionalInterface
    interface RangeReader {
        Optional<byte[]> read(long offset, int length);
    }

    static final class MalformedArchiveException extends RuntimeException {
        MalformedArchiveException(String message) {
            super(message);
        }
    }

    /**
     * ustar 헤더(512B 블록)를 따라가며 {@code fileName} entry 의 본문을 읽는다.
     * <ul>
     *   <li>경로는 tar 최상위 또는 한 단계 디렉터리 밑까지만 인정한다("out/training_metrics.json") —
     *       {@code ..}·절대경로·드라이브 문자가 섞인 이름은 traversal 로 보고 무시한다(§7).
     *   <li>entry 크기가 {@code maxBytes} 를 넘으면 예외 — 조작된 archive 다.
     *   <li>GNU longname('L')·pax 헤더('x'·'g')는 본문을 건너뛴다(우리 이름은 100자 미만).
     * </ul>
     */
    static Optional<byte[]> findTarEntry(RangeReader reader, String fileName, int maxBytes) {
        long offset = 0;
        String pendingLongName = null;   // GNU 'L' entry 가 예고한 "다음 entry 의 진짜 이름"
        for (int entry = 0; entry < MAX_TAR_ENTRIES; entry++) {
            byte[] header = reader.read(offset, TAR_BLOCK).orElse(null);
            if (header == null || header.length < TAR_BLOCK || allZero(header)) {
                return Optional.empty();   // 파일 끝 또는 종료 블록
            }
            String name = tarString(header, 0, 100);
            String prefix = tarString(header, 345, 155);
            if (!prefix.isEmpty()) {
                name = prefix + "/" + name;
            }
            if (pendingLongName != null) {
                name = pendingLongName;   // 100자 초과 경로 — 헤더 name 은 잘려 있다
                pendingLongName = null;
            }
            long size = parseOctal(header, 124, 12);
            if (size < 0) {
                // 부호 있는 8진수로 length 를 음수로 만들면 readRange 가 예외로 터진다 — 조작된 archive
                throw new MalformedArchiveException("negative entry size");
            }
            char typeflag = (char) header[156];
            long dataBlocks = (size + TAR_BLOCK - 1) / TAR_BLOCK;

            if (typeflag == 'L') {
                // GNU longname: 본문이 다음 entry 의 실제 이름이다. 안 읽으면 정상 tar 를 legacy 로 오판한다
                if (size > MAX_LONGNAME_BYTES) {
                    throw new MalformedArchiveException("longname too large: " + size);
                }
                byte[] data = reader.read(offset + TAR_BLOCK, (int) size)
                        .orElseThrow(() -> new MalformedArchiveException("truncated longname"));
                pendingLongName = new String(data, StandardCharsets.UTF_8).trim().replace(" ", "");
                int nul = pendingLongName.indexOf(' ');
                if (nul >= 0) {
                    pendingLongName = pendingLongName.substring(0, nul);
                }
                offset += TAR_BLOCK + dataBlocks * TAR_BLOCK;
                continue;
            }

            if ((typeflag == '0' || typeflag == 0) && isWantedEntry(name, fileName)) {
                if (size > maxBytes) {
                    throw new MalformedArchiveException(fileName + " entry too large: " + size);
                }
                byte[] data = reader.read(offset + TAR_BLOCK, (int) size)
                        .orElseThrow(() -> new MalformedArchiveException("truncated entry body"));
                if (data.length < size) {
                    throw new MalformedArchiveException("truncated entry body");
                }
                return Optional.of(data);
            }
            offset += TAR_BLOCK + dataBlocks * TAR_BLOCK;
        }
        throw new MalformedArchiveException("too many tar entries");
    }

    /** 원하는 파일인가 — traversal 이름은 어떤 경우에도 매칭하지 않는다. */
    static boolean isWantedEntry(String rawName, String fileName) {
        String name = rawName.startsWith("./") ? rawName.substring(2) : rawName;
        if (name.isEmpty() || name.startsWith("/") || name.contains("..") || name.indexOf(':') >= 0 || name.contains("\\")) {
            return false;
        }
        if (name.equals(fileName)) {
            return true;
        }
        int slash = name.indexOf('/');
        return slash > 0 && name.indexOf('/', slash + 1) < 0 && name.substring(slash + 1).equals(fileName);
    }

    private static boolean allZero(byte[] block) {
        for (byte b : block) {
            if (b != 0) {
                return false;
            }
        }
        return true;
    }

    private static String tarString(byte[] header, int offset, int length) {
        int end = offset;
        while (end < offset + length && header[end] != 0) {
            end++;
        }
        return new String(header, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    private static long parseOctal(byte[] header, int offset, int length) {
        String text = tarString(header, offset, length).trim();
        if (text.isEmpty()) {
            return 0;
        }
        try {
            return Long.parseLong(text, 8);
        } catch (NumberFormatException e) {
            throw new MalformedArchiveException("bad octal size field");
        }
    }
}
