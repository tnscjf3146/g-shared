package com.gshared.back.worker.domain;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * GPU 등급 — 같은 그래픽카드를 가진 워커들을 하나로 묶어 본 것.
 *
 * <p>저장된 테이블이 아니다. 워커 1대 = GPU 1개라(V7 에서 {@code jobs.gpu_id} 를 없앤 이유)
 * 등급이라는 행은 어디에도 없고, 조회할 때마다 살아 있는 워커를 모델명으로 묶어 만든다.
 * 사용자는 "누구의 PC"가 아니라 "어떤 등급"을 고르므로 화면에는 이 모양이 필요하다.
 *
 * <p>{@code id} 는 모델명에서 계산한다. 화면이 이 값을 골라 견적 요청에 다시 실어 보내므로
 * 재기동해도 같은 값이어야 하는데, {@link String#hashCode()} 는 자바 명세에 계산식이
 * 고정돼 있어 JVM·기동과 무관하게 같은 값을 준다. 부호 없는 정수로 넓혀 항상 양수로 만든다.
 */
public record GpuGrade(
        long id,
        String model,
        int vramMb,
        List<Worker> workers
) {
    public static long idOf(String gpuModel) {
        return Integer.toUnsignedLong(gpuModel.hashCode());
    }

    /**
     * 워커 목록을 등급으로 묶는다. 스펙(모델명·VRAM)이 비어 있는 워커는 제외한다 —
     * 어느 등급인지 말할 수 없고, Scheduler 도 null 을 422 로 거절한다.
     *
     * <p>같은 모델인데 VRAM 이 다르게 보고된 경우(드라이버·측정 차이)는 가장 작은 값을 쓴다.
     * 등급을 보고 "이만큼은 된다"고 판단하는 화면이라, 큰 쪽을 대표로 삼으면 실제로는
     * 감당 못 하는 워커가 섞인다.
     */
    public static List<GpuGrade> from(List<Worker> workers) {
        Map<String, List<Worker>> byModel = workers.stream()
                .filter(w -> w.getGpuModel() != null && w.getVramMb() != null)
                .collect(Collectors.groupingBy(Worker::getGpuModel));

        return byModel.entrySet().stream()
                .map(entry -> new GpuGrade(
                        idOf(entry.getKey()),
                        entry.getKey(),
                        entry.getValue().stream().mapToInt(Worker::getVramMb).min().orElseThrow(),
                        entry.getValue()))
                // VRAM 큰 순 → 같으면 이름순. 화면이 위에서부터 읽으므로 순서가 매번 같아야 한다.
                .sorted(Comparator.comparingInt(GpuGrade::vramMb).reversed()
                        .thenComparing(GpuGrade::model))
                .toList();
    }
}
