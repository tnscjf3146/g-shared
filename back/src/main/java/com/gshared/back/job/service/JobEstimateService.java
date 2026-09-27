package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.SchedulerErrorCode;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.dto.request.EstimateJobRequest;
import com.gshared.back.job.dto.request.JobSpecRequest;
import com.gshared.back.job.dto.response.EstimateResponse;
import com.gshared.back.job.dto.response.GpuOptionResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.worker.domain.GpuGrade;
import com.gshared.back.worker.service.GpuCatalogService;
import com.gshared.back.workload.domain.BaseModel;
import com.gshared.back.workload.exception.WorkloadErrorCode;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 주문 전 단계 — GPU 후보와 견적.
 *
 * <p>아무것도 저장하지 않는다. 사용자가 화면에서 설정을 바꿀 때마다 불리는 곳이라,
 * 여기서 행을 만들면 버려진 주문서가 쌓인다. 저장은 {@link JobService#create} 만 한다.
 *
 * <p>{@link JobService} 와 학습 설정·비용 계산을 공유한다({@link TrainingConfigResolver},
 * {@link JobCostPolicy}). 견적에서 본 금액과 실제 주문 금액이 달라지면 안 되기 때문이다.
 */
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobEstimateService {

    private final WorkloadRepository workloadRepository;
    private final BaseModelRepository baseModelRepository;
    private final GpuCatalogService gpuCatalogService;
    private final SchedulerClient schedulerClient;
    private final TrainingConfigResolver trainingConfigResolver;
    private final EscrowVerification escrow;

    /**
     * 이 명세를 감당할 수 있는 GPU 등급들과 그 적합도.
     *
     * <p>지금 일할 수 있는 워커가 하나도 없으면 409 다 — 요청이 틀린 게 아니라 기다리면 되는
     * 상황이라, 사용자에게 설정을 고치라고 말하면 안 된다.
     */
    public List<GpuOptionResponse> getGpuOptions(JobSpecRequest request) {
        BaseModel baseModel = loadBaseModel(request);
        List<GpuGrade> grades = approvedOnly(gpuCatalogService.grades());
        if (grades.isEmpty()) {
            throw new BusinessException(JobErrorCode.NO_AVAILABLE_WORKER);
        }

        SchedulerRecommendRequest.DatasetMeta datasetMeta = request.datasetMetadata().toSchedulerMeta();
        SchedulerTrainingConfig config = trainingConfigResolver.resolve(
                request.trainingMode(), request.autoMode(), request.trainingConfig(), datasetMeta);

        // Scheduler 는 워커 단위로 답한다. 어느 등급이 1순위인지 알려면 추천 워커가 어느 등급인지 되짚어야 한다.
        SchedulerRecommendResponse recommendation =
                recommend(request, baseModel, datasetMeta, config, grades);
        Long recommendedGradeId = gradeIdOf(recommendation.recommendedWorkerId(), grades);

        // 필요 VRAM 은 AI 가 이 Job 의 실제 설정으로 계산한 값(Dynamic VRAM)이 정본 — 카탈로그 상수(8192)로
        // 덮어쓰면 가벼운 학습에서도 6GB GPU 가 전부 INSUFFICIENT 로 사라진다. AI 응답에 값이 없으면
        // (구버전/배포 순서) 기존 카탈로그 값으로 후퇴. 이 한 값이 fit 판정과 응답에 똑같이 쓰인다.
        // 0 이하는 계약 위반(AI 스키마는 PositiveInt) — 그대로 쓰면 모든 GPU 가 "가능"이 되므로 fallback 이 안전하다.
        Integer aiRequired = recommendation.requiredVramMb();
        int requiredVramMb = aiRequired != null && aiRequired > 0
                ? aiRequired
                : baseModel.requiredVramMb();
        return grades.stream()
                .map(grade -> toOption(grade, requiredVramMb, recommendedGradeId))
                .toList();
    }

    /**
     * 고른 GPU 등급으로 실행시간과 비용을 뽑는다.
     *
     * <p>등급 안의 워커 중 Scheduler 가 가장 빠르다고 본 값을 쓴다. 같은 등급이어도 벤치마크가
     * 조금씩 달라 예측이 갈리는데, 사용자는 등급을 골랐지 특정 PC 를 고른 것이 아니다.
     */
    public EstimateResponse estimate(EstimateJobRequest request) {
        JobSpecRequest spec = request.spec();
        BaseModel baseModel = loadBaseModel(spec);

        // 목록(gpu-options)·주문(create)과 **같은 후보 집합**을 본다. 여기만 approvedOnly 가 빠져 있었다(수정요청서 BE-12).
        //
        // 방향을 똑바로 적어 둔다: 필터가 없던 쪽은 후보가 **더 넓었다**. 그래서 목록·주문이 거절하는 등급으로도
        // 견적이 나왔다 — "견적은 나오는데 주문이 409" 가 그 증상이다. 반대로 "목록은 되는데 견적이 409" 는
        // 이 필터와 무관하고, 목록을 받은 뒤 워커가 빠진 것이다(findAvailableWorkers: ONLINE·미배정,
        // 또는 benchmark 없는 워커라 recommend 단계에서 빠지는 경우). 두 증상을 섞어 읽지 말 것.
        //
        // 고른 등급을 먼저 찾고 **그 등급에만** 승인 확인을 돌린다. approvedOnly 안의
        // escrow.operatorApproved 는 캐시 없는 체인 RPC(호출당 최대 10초)이고, 이 메서드는 사용자가 GPU 를
        // 누를 때마다 불린다 — 전체 등급에 돌리면 온라인 워커 수만큼 RPC 가 트랜잭션 안에서 줄지어 나간다
        // (수정요청서 BE-11 이 경고하는 바로 그 모양). 필요한 것은 고른 등급 하나뿐이다.
        GpuGrade selected = gpuCatalogService.grades().stream()
                .filter(g -> g.id() == request.gpuId())
                .findFirst()
                // 조회 때 있던 등급이 사라졌다는 것은 그 PC 들이 모두 꺼졌다는 뜻 — 기다리면 되는 상황이다.
                .orElseThrow(() -> new BusinessException(JobErrorCode.NO_AVAILABLE_WORKER));
        GpuGrade grade = approvedOnly(List.of(selected)).stream()
                .findFirst()
                // 등급은 살아 있는데 그 안에 승인된 워커가 없다 — 목록·주문에서도 빠지는 등급이라 여기서도 뺀다
                .orElseThrow(() -> new BusinessException(JobErrorCode.NO_AVAILABLE_WORKER));

        SchedulerRecommendRequest.DatasetMeta datasetMeta = spec.datasetMetadata().toSchedulerMeta();
        SchedulerTrainingConfig config = trainingConfigResolver.resolve(
                spec.trainingMode(), spec.autoMode(), spec.trainingConfig(), datasetMeta);

        SchedulerRecommendResponse recommendation =
                recommend(spec, baseModel, datasetMeta, config, List.of(grade));

        double predictedRuntimeSec = recommendation.rankings().stream()
                .mapToDouble(SchedulerRecommendResponse.Ranking::predictedRuntimeSec)
                .min()
                .orElseThrow(() -> new BusinessException(SchedulerErrorCode.NO_FEASIBLE_GPU));

        long estimatedCost = JobCostPolicy.estimate(datasetMeta.effectiveTokens(), config.epochs());

        return new EstimateResponse(config, predictedRuntimeSec, estimatedCost);
    }

    /**
     * 카탈로그 확인. 모델이 그 이미지에 속하지 않는 조합은 여기서 막는다 —
     * 주문 단계에서만 막으면, 견적까지 통과한 조합이 마지막에 거절당한다.
     * 모델을 돌려주는 것은 VRAM 하한이 모델에 달려 있어서다({@link BaseModel#requiredVramMb()}) — 워크로드는 모델을 타고 간다.
     */
    private BaseModel loadBaseModel(JobSpecRequest request) {
        workloadRepository.findByIdAndEnabledTrue(request.workloadId())
                .orElseThrow(() -> new BusinessException(WorkloadErrorCode.WORKLOAD_NOT_FOUND));
        BaseModel baseModel = baseModelRepository.findByIdAndEnabledTrue(request.baseModelId())
                .orElseThrow(() -> new BusinessException(WorkloadErrorCode.BASE_MODEL_NOT_FOUND));
        if (!baseModel.getWorkload().getId().equals(request.workloadId())) {
            throw new BusinessException(WorkloadErrorCode.BASE_MODEL_NOT_FOUND);
        }
        return baseModel;
    }

    /**
     * operator 승인이 확인된 워커만 남긴 등급들.
     *
     * <p>주문({@link JobService#create})은 이 조건으로 후보를 거르는데 견적에는 없어서, 승인 안 된
     * 공급자만 있는 등급이 화면에는 "추천" 으로 뜨고 주문에서 409 로 거절됐다. 사용자는 화면이
     * 고르라고 한 GPU 를 골랐을 뿐인데 기다려도 풀리지 않는다 — 공급자가 온체인에서 키를
     * 승인하기 전에는 아무것도 바뀌지 않기 때문이다. 두 곳이 같은 목록을 봐야 한다.
     *
     * <p>{@code isContradicted()} 만 거른다 — 체인을 읽지 못했을 때까지 빼면 노드가 잠깐 답하지
     * 않는 동안 멀쩡한 등급이 목록에서 사라진다({@link JobService#create} 와 같은 판단).
     */
    private List<GpuGrade> approvedOnly(List<GpuGrade> grades) {
        return grades.stream()
                .map(grade -> new GpuGrade(grade.id(), grade.model(), grade.vramMb(),
                        grade.workers().stream()
                                .filter(w -> !escrow.operatorApproved(
                                        w.getOwner().getWalletAddress(), w.getOperatorAddress()).isContradicted())
                                .toList()))
                .filter(grade -> !grade.workers().isEmpty())
                .toList();
    }

    private SchedulerRecommendResponse recommend(JobSpecRequest request, BaseModel baseModel,
                                                 SchedulerRecommendRequest.DatasetMeta datasetMeta,
                                                 SchedulerTrainingConfig config,
                                                 List<GpuGrade> grades) {
        List<SchedulerRecommendRequest.GpuCandidate> candidates = grades.stream()
                .flatMap(grade -> grade.workers().stream())
                // 벤치마크가 없는 워커는 후보에서 뺀다 — Scheduler 가 null 을 422 로 거절한다.
                // (benchmarkPeakVramMb 는 예외 — 구버전 exe 워커는 없고, 그건 후보에서 뺄 이유가 아니다)
                .filter(worker -> worker.getBenchmarkTokensPerSec() != null)
                .map(worker -> new SchedulerRecommendRequest.GpuCandidate(
                        worker.getId(), worker.getGpuModel(), worker.getVramMb(),
                        worker.getBenchmarkTokensPerSec(), worker.getBenchmarkPeakVramMb()))
                .toList();
        if (candidates.isEmpty()) {
            throw new BusinessException(JobErrorCode.NO_AVAILABLE_WORKER);
        }

        SchedulerRecommendResponse response = schedulerClient.recommend(new SchedulerRecommendRequest(
                request.baseModelId(), baseModel.getWorkload().getId(), datasetMeta, config,
                new SchedulerRecommendRequest.Requirements(baseModel.requiredVramMb()), candidates));
        // 2xx 지만 추천이 비어있는 경우 — "실행 가능한 GPU 없음"으로 명확히 (미매핑 500 방지)
        if (response.rankings() == null || response.rankings().isEmpty()) {
            throw new BusinessException(SchedulerErrorCode.NO_FEASIBLE_GPU);
        }
        return response;
    }

    /** 추천 워커가 속한 등급. Scheduler 가 아무도 고르지 않았으면 null. */
    private Long gradeIdOf(String recommendedWorkerId, List<GpuGrade> grades) {
        if (recommendedWorkerId == null) {
            return null;
        }
        Map<String, Long> gradeIdByWorker = grades.stream()
                .flatMap(grade -> grade.workers().stream()
                        .map(worker -> Map.entry(worker.getId(), grade.id())))
                .collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));
        return gradeIdByWorker.get(recommendedWorkerId);
    }

    private GpuOptionResponse toOption(GpuGrade grade, int requiredVramMb, Long recommendedGradeId) {
        boolean recommended = recommendedGradeId != null && recommendedGradeId == grade.id();
        GpuOptionResponse.Fit fit = GpuFitPolicy.fitOf(grade.vramMb(), requiredVramMb, recommended);
        return new GpuOptionResponse(
                // vramMb 는 raw telemetry 그대로 — 판정에만 nominal 값을 쓴다({@link GpuFitPolicy})
                grade.id(), grade.model(), grade.vramMb(),
                // 시간당 단가는 정책 미정 — GpuResponse 와 같은 이유로 0 이다.
                0L,
                fit, requiredVramMb, GpuFitPolicy.reasonOf(fit, grade.vramMb(), requiredVramMb),
                grade.workers().size());
    }
}
