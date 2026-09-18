"""Runtime 예측 요청과 응답. Backend가 후보 GPU를 이미 골라 보낸다.

이 경계에서 Scheduler가 하는 일은 하나다: 받은 GPU 후보마다 "이 작업이 이 GPU에서
몇 초 걸릴지"를 답한다. 순위를 매기지 않고, 하나를 고르지도 않고, 비용을 계산하지도
않는다. 그건 각각 다음 작업과 Backend의 책임이다.

Worker가 온라인인지, 놀고 있는지, 이미지가 깔려 있는지도 판단하지 않는다.
:class:`GpuCandidate` 로 온 것은 Backend가 이미 "지금 이 작업을 받을 수 있다"고
판단한 GPU라는 뜻이다.
"""

from typing import Annotated

from pydantic import Field

from app.schemas.base import SchedulerSchema
from app.schemas.dataset import DatasetMetadata
from app.schemas.training import TrainingConfig

#: DB PK로 오는 값도 이 타입을 쓴다. 0과 음수인 PK는 없으므로 ``gt=0`` 이 곧
#: "실재하는 행을 가리키는가"에 대한 검사가 된다.
PositiveInt = Annotated[int, Field(gt=0)]

#: ``gt=0`` 만으로는 무한대가 통과한다 -- ``inf > 0`` 은 참이다. NaN은 비교가 모두
#: 거짓이라 걸리지만 무한대는 그대로 예측 입력이 되어, 초당 무한 토큰인 GPU가
#: 0초에 끝난다는 답을 만들어 낸다. 그래서 명시적으로 막는다.
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class RuntimeDatasetMetadata(DatasetMetadata):
    """Runtime 예측이 쓰는 데이터셋 정보.

    :class:`~app.schemas.dataset.DatasetMetadata` 를 그대로 물려받고 한 필드만
    더한다. 기존 metadata를 고치지 않은 것은 의도한 선택이다 -- ``/scheduler/input``
    과 AUTO는 이미 그 계약으로 동작 중이고, 거기에 필수 필드를 하나 추가하면
    지금 잘 돌아가는 두 엔드포인트가 그날로 깨진다.

    ``avgSeqLength <= maxSeqLength`` 검증도 상속되므로 여기서 다시 쓰지 않는다.
    """

    effective_tokens: PositiveInt = Field(
        description=(
            "GPU가 실제로 계산하게 될 토큰 수. totalTokens는 데이터셋에 담긴 토큰이고 "
            "이 값은 maxLength로 자르고 채운 뒤 GPU에 올라가는 양이라, 실행 시간에 "
            "직접 대응하는 쪽은 이 값이다. 두 값은 다를 수 있고 어느 쪽이 큰지도 "
            "정해져 있지 않다 -- 자르면 줄고 패딩하면 는다. Backend가 계산해서 보낸다."
        ),
    )


class GpuCandidate(SchedulerSchema):
    """Backend가 "이 작업을 받을 수 있다"고 판단한 GPU 하나.

    ``workerId`` 는 답을 돌려줄 때 어느 GPU 이야기인지 알기 위한 꼬리표일 뿐,
    예측에 쓰이지 않는다. 식별자를 학습하면 모델이 관계 대신 이름을 외운다.
    """

    worker_id: str = Field(
        min_length=1,
        description=(
            "예측 결과를 이 GPU에 도로 붙이기 위한 식별자. Feature가 아니다."
        ),
    )
    gpu_model: str = Field(
        min_length=1,
        description="GPU 이름. 같은 모델이라도 개체마다 성능이 조금씩 다르다.",
    )
    vram_mb: PositiveInt = Field(
        description=(
            "이 GPU의 VRAM(MB). 실행 시간과 단순 반비례하지 않는다 -- 설정이 들어갈 "
            "만큼 넉넉하면 더 많다고 빨라지지 않고, 빠듯해지기 시작할 때만 느려진다."
        ),
    )
    benchmark_tokens_per_sec: PositiveFloat = Field(
        description=(
            "고정된 Transformer Fine-tuning workload에서 이 GPU가 실제로 처리한 "
            "초당 토큰 수. Worker가 등록될 때 한 번 측정한 실측값이다 "
            "(System의 GPU Benchmark 참고). 예측에서 GPU 속도를 나타내는 값은 "
            "이것 하나이며, 예전의 benchmarkScore는 쓰지 않는다."
        ),
    )
    benchmark_peak_vram_mb: PositiveInt | None = Field(
        default=None,
        description=(
            "그 벤치마크가 이 GPU에서 실제로 쓴 최대 VRAM(MB). Worker가 등록할 때 "
            "이미 재고 있는 값이며, vramMb(드라이버가 보고하는 카드 총량)와 다르다 "
            "-- 총량에는 드라이버와 화면 출력이 이미 쓰고 있는 몫이 들어 있어, "
            "학습에 실제로 내줄 수 있는 양을 말해 주지 않는다.\n\n"
            "**선택 필드다.** 이 값을 보내지 않는 Backend와 이 값을 모르는 옛 "
            "Worker가 등록한 GPU가 함께 있는 동안에도 요청이 거절되면 안 되므로 "
            "None을 허용한다. 지금은 계약으로만 받아 두고 예측 feature에는 넣지 "
            "않는다 -- feature 목록을 바꾸면 모델을 다시 학습해야 하고, 값이 한 "
            "대도 쌓이지 않은 열은 예측을 나아지게 하지 못한다 "
            "(app.scheduler.runtime_features의 FEATURE_SCHEMA_VERSION 참고)."
        ),
    )


class RuntimePredictionRequest(SchedulerSchema):
    """Backend → AI Scheduler. 작업 하나와 그 작업을 돌릴 수 있는 GPU들."""

    model_id: PositiveInt = Field(
        description=(
            "Base Model database identifier. Backend의 base_models PK 그대로다. "
            "학습할 기반 모델이 무엇인지가 실행 시간을 크게 좌우하므로 예측에 쓰인다. "
            "정수지만 크기에 의미는 없다 -- 1보다 2가 큰 모델이라는 뜻이 아니라서 "
            "범주형으로 다룬다(app.scheduler.runtime_features 참고)."
        ),
    )
    docker_image_id: PositiveInt = Field(
        description=(
            "Approved Docker Image database identifier. Backend의 workloads PK 그대로다. "
            "계약에는 남기지만 현재 모델의 feature에는 들어가지 않는다 -- "
            "이유는 app.scheduler.runtime_features 참고."
        ),
    )
    dataset: RuntimeDatasetMetadata = Field(description="학습에 쓸 데이터셋")
    training_config: TrainingConfig = Field(description="확정된 학습 설정")
    gpu_candidates: list[GpuCandidate] = Field(
        min_length=1,
        description=(
            "Backend가 이미 걸러낸 실행 가능한 GPU들. 하나씩 따로 요청하지 않도록 "
            "한 번에 여러 개를 받는다. 비어 있으면 예측할 대상이 없으므로 거절한다."
        ),
    )


class RuntimePrediction(SchedulerSchema):
    """GPU 하나에 대한 답."""

    worker_id: str = Field(description="요청에 들어 있던 그 GPU")
    predicted_runtime_sec: PositiveFloat = Field(
        description=(
            "이 GPU에서 이 작업이 걸릴 것으로 예측한 시간(초). 0 이하나 NaN은 "
            "예측이 아니므로 그런 값은 응답이 되지 못한다."
        ),
    )


class RuntimePredictionResponse(SchedulerSchema):
    """AI Scheduler → Backend.

    순위도, 추천도 없다. 후보마다 예측 하나씩이고 순서는 요청과 같다.
    어느 GPU를 쓸지 고르는 것은 다음 작업(gpu-ranking)의 책임이다.
    """

    scheduler_model_version: str = Field(
        description=(
            "이 예측을 만든 모델의 버전. 나중에 실제 실행 시간과 대조할 때 "
            "어느 모델이 뭐라고 했는지 알 수 있도록 답에 실어 보낸다."
        ),
    )
    predictions: list[RuntimePrediction] = Field(
        description="요청의 gpuCandidates와 같은 순서, 같은 개수",
    )
