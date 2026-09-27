"""불러 둔 Runtime 모델을 들고 있는 곳.

요청마다 디스크에서 읽거나 다시 학습하지 않는다. 모델은 시작할 때 한 번 읽고
메모리에 남는다. 매 요청마다 읽으면 XGBoost 파일 파싱이 예측보다 오래 걸리고,
요청 도중에 파일이 바뀌면 같은 요청 안에서 서로 다른 모델이 답하게 된다.

**모델이 없다고 서비스 전체가 죽지는 않는다.** ``/health`` 와 ``/scheduler/input``,
AUTO 설정 생성은 Runtime 모델과 아무 상관이 없고, 그것들까지 같이 못 쓰게 만들
이유가 없다. 대신 Runtime 예측 엔드포인트만 왜 못 쓰는지 말하며 거절한다.
가짜 예측으로 대신하지 않는다.

**맞지 않는 모델은 그렇게 다루지 않는다.** 학습되지 않은 것은 학습을 돌리면 풀리는
운영 상태지만, 지금 빌드와 다른 계약으로 학습된 artifact가 놓여 있는 것은 배포가
잘못 물렸다는 뜻이다. 그것을 "없는 것"과 같이 취급하면 앱은 멀쩡히 뜨고 ``/health``
도 통과하는데 예측을 부를 때마다 실패한다 -- 문제는 기동 환경에 있는데 드러나는
곳은 요청이라, 보는 쪽은 요청을 의심하게 된다. 그래서
:class:`~app.scheduler.runtime_predictor.IncompatibleModelError` 만은 붙잡지 않고
올려 보내 **시작 자체를 실패시킨다.**
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.scheduler.runtime_predictor import (
    IncompatibleModelError,
    ModelUnavailableError,
    RuntimePredictor,
    load_runtime_predictor,
)

#: 기본 위치는 저장소의 ``ai/models`` 다. 환경변수로 바꿀 수 있게 둔 것은 배포
#: 환경마다 경로가 다르기 때문이고, 테스트도 이 문을 쓴다.
_ENV_MODELS_DIRECTORY = "GSHARED_SCHEDULER_MODELS_DIR"


def models_directory() -> Path:
    configured = os.environ.get(_ENV_MODELS_DIRECTORY)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "models"


@dataclass(frozen=True)
class LoadedRuntimeModel:
    """시작할 때 모델을 읽어 본 결과. 성공이거나, 왜 실패했는지다."""

    predictor: RuntimePredictor | None
    error: str | None

    @property
    def is_available(self) -> bool:
        return self.predictor is not None

    @classmethod
    def load_from(cls, directory: Path) -> "LoadedRuntimeModel":
        try:
            return cls(load_runtime_predictor(directory), None)
        except IncompatibleModelError:
            # 이것만은 붙잡지 않는다. 이유는 이 파일 맨 위에 적었다.
            raise
        except ModelUnavailableError as error:
            # 실패는 실패로 남긴다. 예측을 요청받을 때 이 문장을 그대로 돌려준다.
            return cls(None, str(error))
