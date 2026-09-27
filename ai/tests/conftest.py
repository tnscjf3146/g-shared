"""Runtime 예측 테스트가 함께 쓰는 것들.

여기서 학습하는 모델은 작다. 1200행짜리 부트스트랩을 테스트마다 다시 학습하면
테스트가 느려지고, 느린 테스트는 결국 아무도 돌리지 않는다. 파이프라인이 도는지를
보는 데는 몇백 행이면 충분하고, 관계가 실제로 학습되는지는 그 안에서도 확인된다.
"""

from pathlib import Path

import pytest

from app.training.runtime_dataset import build_rows
from app.training.runtime_trainer import train_runtime_model

#: 테스트용 행 수. 검증셋을 뗄 만큼은 되고, 학습이 1초 안에 끝날 만큼 작다.
TEST_ROW_COUNT = 300


@pytest.fixture(scope="session")
def bootstrap_rows() -> list[dict]:
    """SYNTHETIC 학습 행. 실제 실행 기록이 아니다."""
    return build_rows(TEST_ROW_COUNT)


@pytest.fixture(scope="session")
def trained_models_directory(tmp_path_factory, bootstrap_rows) -> Path:
    """한 번 학습해 둔 모델이 있는 디렉터리. 세션 전체가 공유한다."""
    directory = tmp_path_factory.mktemp("runtime-model")
    train_runtime_model(bootstrap_rows, models_directory=directory)
    return directory


@pytest.fixture(scope="session")
def predictor(trained_models_directory):
    from app.scheduler.runtime_predictor import load_runtime_predictor

    return load_runtime_predictor(trained_models_directory)
