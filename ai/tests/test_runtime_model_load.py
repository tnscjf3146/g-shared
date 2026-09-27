"""모델을 불러오는 길, 그리고 불러오면 안 되는 경우들.

여기서 거절하지 못한 모델은 조용히 틀린 답을 낸다. 열이 다른 모델도, 타깃 변환이
다른 모델도 예외를 던지지 않고 그럴듯한 숫자를 내놓기 때문에, 파일을 여는 이 지점이
마지막으로 막을 수 있는 곳이다.
"""

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scheduler.runtime_features import FEATURE_SCHEMA_VERSION
from app.scheduler.runtime_model import LoadedRuntimeModel
from app.scheduler.runtime_predictor import (
    IncompatibleModelError,
    ModelUnavailableError,
    load_runtime_predictor,
)
from app.training.runtime_trainer import (
    METADATA_FILE_NAME,
    MODEL_FILE_NAME,
    MODEL_VERSION,
)

#: 정수 modelId 이전의 schema. 그때 artifact의 categoryMappings는 모델 이름
#: 문자열을 담고 있어서, 지금 오는 정수 modelId와는 한 값도 맞지 않는다.
LEGACY_SCHEMA_VERSION = "runtime-features-v1"


@pytest.fixture
def directory(tmp_path, trained_models_directory):
    """정상 모델 한 벌을 망가뜨려 볼 수 있는 사본."""
    for name in (MODEL_FILE_NAME, METADATA_FILE_NAME):
        shutil.copy(trained_models_directory / name, tmp_path / name)
    return tmp_path


def rewrite_metadata(directory, **changes):
    path = directory / METADATA_FILE_NAME
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.update(changes)
    path.write_text(json.dumps(metadata), encoding="utf-8")


class TestLoadingAGoodModel:
    def test_it_loads(self, directory):
        assert load_runtime_predictor(directory) is not None

    def test_it_carries_the_version_the_response_will_report(self, directory):
        assert load_runtime_predictor(directory).model_version == MODEL_VERSION

    def test_it_carries_the_category_table(self, directory):
        predictor = load_runtime_predictor(directory)

        assert predictor.categories.known_values("gpu_model")

    def test_it_knows_it_was_trained_on_synthetic_data(self, directory):
        # 이 사실이 따라다녀야 나중에 예측을 얼마나 믿을지 판단할 수 있다.
        assert load_runtime_predictor(directory).training_data_type == (
            "synthetic-bootstrap")


class TestWhatIsRefused:
    def test_no_model_file(self, directory):
        (directory / MODEL_FILE_NAME).unlink()

        with pytest.raises(ModelUnavailableError, match="no runtime model"):
            load_runtime_predictor(directory)

    def test_no_metadata_beside_it(self, directory):
        # metadata에 카테고리 표가 있다. 없으면 문자열을 무엇으로 바꿀지 알 수 없다.
        (directory / METADATA_FILE_NAME).unlink()

        with pytest.raises(ModelUnavailableError, match="metadata"):
            load_runtime_predictor(directory)

    def test_an_empty_directory(self, tmp_path):
        with pytest.raises(ModelUnavailableError):
            load_runtime_predictor(tmp_path)

    def test_metadata_that_is_not_json(self, directory):
        (directory / METADATA_FILE_NAME).write_text("{ half written", encoding="utf-8")

        with pytest.raises(ModelUnavailableError, match="could not be read"):
            load_runtime_predictor(directory)

    def test_a_different_feature_schema_version(self, directory):
        rewrite_metadata(directory, featureSchemaVersion="runtime-features-v0")

        with pytest.raises(ModelUnavailableError, match="feature schema"):
            load_runtime_predictor(directory)

    def test_different_feature_columns(self, directory):
        # 버전은 같은데 열이 다르면 둘 중 하나가 버전을 올리지 않고 바뀐 것이다.
        rewrite_metadata(directory, featureNames=["epochs", "batch_size"])

        with pytest.raises(ModelUnavailableError, match="different feature columns"):
            load_runtime_predictor(directory)

    def test_a_different_target_transform(self, directory):
        # log로 학습된 모델을 그대로 초로 읽으면 하루짜리 작업이 11초가 된다.
        rewrite_metadata(directory, targetTransform="none")

        with pytest.raises(ModelUnavailableError, match="target transform"):
            load_runtime_predictor(directory)

    def test_missing_category_mappings(self, directory):
        rewrite_metadata(directory, categoryMappings=None)

        with pytest.raises(ModelUnavailableError, match="category mappings"):
            load_runtime_predictor(directory)

    def test_a_corrupted_model_file(self, directory):
        (directory / MODEL_FILE_NAME).write_text("not a model at all", encoding="utf-8")

        with pytest.raises(ModelUnavailableError, match="could not be loaded"):
            load_runtime_predictor(directory)


class TestTheHolder:
    """서비스가 들고 있는 모양. 실패해도 앱은 뜬다."""

    def test_a_good_model_is_available(self, directory):
        loaded = LoadedRuntimeModel.load_from(directory)

        assert loaded.is_available
        assert loaded.error is None

    def test_a_missing_model_is_not_an_exception_but_a_reason(self, tmp_path):
        # 앱이 뜨지 못하면 모델과 무관한 엔드포인트까지 같이 죽는다.
        loaded = LoadedRuntimeModel.load_from(tmp_path)

        assert not loaded.is_available
        assert "no runtime model" in loaded.error

    def test_the_reason_says_how_to_fix_it(self, tmp_path):
        assert "train" in LoadedRuntimeModel.load_from(tmp_path).error.lower()

    def test_no_fake_predictor_is_substituted(self, tmp_path):
        assert LoadedRuntimeModel.load_from(tmp_path).predictor is None


class TestAModelThatDoesNotMatchThisBuild:
    """읽히기는 하는데 다른 계약으로 학습된 모델. 없는 것과 다르게 다룬다.

    없는 모델은 학습하면 풀리지만 이건 배포가 잘못 물린 것이라, 붙잡아 두고
    요청 때 돌려주면 문제가 있는 곳(기동 환경)과 드러나는 곳(요청)이 어긋난다.
    """

    def test_a_stale_feature_schema_is_not_swallowed(self, directory):
        rewrite_metadata(directory, featureSchemaVersion=LEGACY_SCHEMA_VERSION)

        with pytest.raises(IncompatibleModelError):
            LoadedRuntimeModel.load_from(directory)

    def test_the_message_names_both_versions(self, directory):
        # 어느 쪽을 고쳐야 하는지 알려면 둘 다 보여야 한다.
        rewrite_metadata(directory, featureSchemaVersion=LEGACY_SCHEMA_VERSION)

        with pytest.raises(IncompatibleModelError) as raised:
            LoadedRuntimeModel.load_from(directory)

        assert FEATURE_SCHEMA_VERSION in str(raised.value)
        assert LEGACY_SCHEMA_VERSION in str(raised.value)

    def test_different_feature_columns_stop_it_too(self, directory):
        rewrite_metadata(directory, featureNames=["epochs", "batch_size"])

        with pytest.raises(IncompatibleModelError):
            LoadedRuntimeModel.load_from(directory)

    def test_a_different_target_transform_stops_it_too(self, directory):
        rewrite_metadata(directory, targetTransform="none")

        with pytest.raises(IncompatibleModelError):
            LoadedRuntimeModel.load_from(directory)

    def test_a_missing_model_is_still_only_a_reason(self, tmp_path):
        # 경계를 못 박는다. 시작을 막는 것은 "맞지 않는 모델"뿐이다.
        loaded = LoadedRuntimeModel.load_from(tmp_path)

        assert not loaded.is_available
        assert loaded.error is not None


class TestWhenItIsFound:
    """mismatch가 드러나는 시점. 요청이 아니라 기동이어야 한다."""

    def test_a_stale_artifact_stops_the_app_from_starting(self, directory, monkeypatch):
        rewrite_metadata(directory, featureSchemaVersion=LEGACY_SCHEMA_VERSION)
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(directory))

        with pytest.raises(IncompatibleModelError):
            with TestClient(app):
                pass

    def test_the_current_artifact_starts_normally(self, directory, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(directory))

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    def test_a_missing_model_still_lets_the_app_start(self, tmp_path, monkeypatch):
        # 이 동작은 그대로다. 예측만 503으로 거절한다.
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
