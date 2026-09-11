"""학습을 한 번 돌린다: ``python -m app.training.train_runtime``

읽는 파일은 둘이다.

* ``data/runtime_bootstrap_synthetic.jsonl`` -- 합성. 지금 모델은 이것으로 만든다.
* ``data/runtime_measured.jsonl`` -- 실제 측정 기록. 학습에 들어갈 수 있는 것만
  들어가고, 나머지는 제외된 이유가 출력된다.

실제 Job History가 쌓이면 합성 파일을 빼면 된다. 파이프라인은 그대로다.

마지막에 실측 두 건과 대 보는 것은 정확도 평가가 아니다. 합성 생성기의 물리 가정이
현실과 **몇 배** 어긋나는지 보기 위한 것이고, 그 배수가 보고서에 그대로 들어간다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.training.measured_runs import (
    ALL_RUNS,
    MEASURED_FILE_NAME,
    MEASURED_RUNS,
    trainable_rows,
    write_measured,
)
from app.training.provenance import select_trainable
from app.training.runtime_dataset import (
    BOOTSTRAP_FILE_NAME,
    TRAINING_DATA_TYPE,
    read_rows,
    write_bootstrap,
)
from app.training.runtime_trainer import train_runtime_model

DATA_DIRECTORY = Path(__file__).resolve().parents[2] / "data"
MODELS_DIRECTORY = Path(__file__).resolve().parents[2] / "models"


def main() -> int:
    path = DATA_DIRECTORY / BOOTSTRAP_FILE_NAME
    if not path.is_file():
        print(f"no dataset at {path}; writing a synthetic bootstrap one")
        path = write_bootstrap(DATA_DIRECTORY)

    rows = read_rows(path)
    print(f"dataset : {path.name} ({len(rows)} rows)")
    print(f"type    : {TRAINING_DATA_TYPE}")

    # 측정 기록은 항상 다시 쓴다. 모듈이 원본이고 파일은 그 사본이라, 둘이 어긋나면
    # 파일 쪽이 오래된 것이다.
    measured_path = write_measured(DATA_DIRECTORY)
    usable = trainable_rows()
    print(f"measured: {MEASURED_FILE_NAME} ({len(ALL_RUNS)} records, "
          f"{len(MEASURED_RUNS)} full jobs, {len(usable)} usable for training)")
    if not usable:
        print("  NOTE: the measured runs carry no catalog model_id, so none of")
        print("        them can be trained on yet. They are compared against")
        print("        the finished model below instead.")

    rows = list(rows) + usable
    report = train_runtime_model(rows, models_directory=MODELS_DIRECTORY)

    print(f"trained : {report.summary()}")
    print(f"  train/validation rows: {report.train_rows} / {report.validation_rows}")
    print(f"  model   : {report.model_path}")
    print(f"  metadata: {report.metadata_path}")

    _print_provenance(rows)
    _print_baselines(report)
    _print_sanity_check()

    if report.training_data_type == TRAINING_DATA_TYPE:
        print("  NOTE: bootstrap synthetic validation -- pipeline check only,")
        print("        these metrics are not production accuracy.")
    return 0


def _print_provenance(rows) -> None:
    _, counts = select_trainable(rows)
    print("  provenance: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


def _print_baselines(report) -> None:
    if not report.baselines:
        return
    print("  baselines (validation MAPE, lower is better):")
    for entry in report.baselines["scores"]:
        print(f"    {entry['name']:<12} {entry['mapePercent']:>8.2f}%"
              f"   MAE {entry['maeSec']:,.1f}s")
    verdict = ("beats them" if report.baselines["beatsBaselines"]
               else "does NOT beat them")
    print(f"    -> xgboost {verdict}; best = {report.baselines['best']}")


def _print_sanity_check() -> None:
    """끝난 모델을 실측 두 건에 대 본다. 실패해도 학습을 되돌리지 않는다."""
    from app.scheduler.runtime_model import LoadedRuntimeModel
    from app.training.sanity_check import compare_against_measured

    loaded = LoadedRuntimeModel.load_from(MODELS_DIRECTORY)
    if not loaded.is_available:
        print(f"  sanity check skipped: {loaded.error}")
        return
    model_ids = loaded.predictor.categories.known_values("model_id")
    comparisons = compare_against_measured(
        loaded.predictor, MEASURED_RUNS, model_ids)
    print("  measured-run sanity check (NOT an accuracy claim -- two points):")
    for comparison in comparisons:
        print(f"    {comparison.summary()}")


if __name__ == "__main__":
    sys.exit(main())
