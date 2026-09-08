"""학습을 한 번 돌린다: ``python -m app.training.train_runtime``

실제 Job History가 없어 synthetic 부트스트랩을 쓴다. 데이터가 생기면 여기서
읽는 파일만 바꾸면 된다 -- 파이프라인은 그대로다.
"""

from __future__ import annotations

import sys
from pathlib import Path

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

    report = train_runtime_model(rows, models_directory=MODELS_DIRECTORY)

    print(f"trained : {report.summary()}")
    print(f"  train/validation rows: {report.train_rows} / {report.validation_rows}")
    print(f"  model   : {report.model_path}")
    print(f"  metadata: {report.metadata_path}")
    if report.training_data_type == TRAINING_DATA_TYPE:
        print("  NOTE: bootstrap synthetic validation -- pipeline check only,")
        print("        these metrics are not production accuracy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
