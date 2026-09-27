"""``python -m app.training.retrain_runtime`` -- 이력으로 후보를 하나 만든다.

기존 ``train_runtime`` 은 그대로 둔다. 그것은 부트스트랩으로 **현역** 모델을
만드는 명령이고, 이것은 이력을 섞어 **후보** 를 만드는 명령이다. 두 파일을 나눈
이유는 실수로 현역을 덮어쓰는 경로를 없애기 위해서다 -- 이 명령은
``models/candidates/`` 아래에만 쓴다.

출력은 두 벌의 지표를 따로 보여 준다. 하나로 합치면 합성 데이터의 오차가
정확도처럼 읽힌다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .retrain import CANDIDATE_VERSION, RetrainingReport, retrain
from .runtime_dataset import BOOTSTRAP_FILE_NAME
from .runtime_history import FileHistorySource

AI_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY = AI_ROOT / "data" / "runtime_history.jsonl"
DEFAULT_BOOTSTRAP = AI_ROOT / "data" / BOOTSTRAP_FILE_NAME
DEFAULT_MODELS = AI_ROOT / "models"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrain the runtime predictor from completed job history. "
                    "Writes a candidate; never touches the incumbent.")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY,
                        help="JSONL of finished jobs (default: %(default)s)")
    parser.add_argument("--bootstrap", type=Path, default=DEFAULT_BOOTSTRAP,
                        help="synthetic rows to keep in the mix")
    parser.add_argument("--models", type=Path, default=DEFAULT_MODELS,
                        help="where the incumbent lives; the candidate goes "
                             "into candidates/ beneath it")
    parser.add_argument("--version", default=CANDIDATE_VERSION,
                        help="candidate name (default: %(default)s)")
    arguments = parser.parse_args()

    if not arguments.history.is_file():
        print(f"No history at {arguments.history}.")
        print("Nothing measured to retrain on yet -- see "
              "docs/runtime-history-contract.md for what the Backend has to "
              "provide.")
        return 1

    report = retrain(
        history=FileHistorySource(arguments.history),
        bootstrap_path=arguments.bootstrap,
        models_directory=arguments.models,
        candidate_version=arguments.version,
    )
    _print(report)
    return 0


def _print(report: RetrainingReport) -> None:
    print(f"\ncandidate      {report.candidate_version}")
    print(f"               {report.candidate_directory}")
    print(f"incumbent      {report.incumbent_version} "
          f"({'untouched' if report.incumbent_untouched else 'CHANGED'})")

    print("\nrows")
    print(f"  bootstrap    {report.bootstrap_row_count}")
    print(f"  measured     {report.measured_row_count}")
    print(f"  rejected     {report.rejected_row_count} "
          f"{report.rejection_reasons or ''}")
    if report.status_counts:
        print(f"  statuses     {report.status_counts}")
    print(f"  measured GPU {report.measured_gpu_models or 'none'}")

    print("\nBootstrap Validation")
    _scores(report.bootstrap_validation)
    print("\nMeasured Validation")
    _scores(report.measured_validation)
    print(f"\nCross-GPU            {report.cross_gpu}")
    print(f"Production Accuracy  {report.production_accuracy}")
    print(f"Production Ready     {report.production_ready}")

    if report.training.baselines:
        print("\nbaselines (bootstrap validation set)")
        for score in report.training.baselines["scores"]:
            print(f"  {score['name']:<12} MAE {score['maeSec']:>12,.2f}s  "
                  f"MAPE {score['mapePercent']:>8.2f}%")

    if report.limitations:
        print("\nlimitations")
        for note in report.limitations:
            print(f"  - {note}")


def _scores(scored) -> None:
    if scored.measured:
        print(f"  rows {scored.row_count}  MAE {scored.mae_sec:,.2f}s  "
              f"MAPE {scored.mape_percent:.2f}%")
    else:
        print(f"  rows {scored.row_count}  -- not scored")
    print(f"  {scored.note}")


if __name__ == "__main__":
    raise SystemExit(main())
