#!/usr/bin/env sh
set -eu

PYTHON_BIN="${PYTHON_BIN:-python3}"
TORCH_VERSION="${TORCH_VERSION:-2.12.1}"
CUDA_WHEEL="${CUDA_WHEEL:-cu126}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKER_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

"$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ is required')"
"$PYTHON_BIN" -m venv "$WORKER_DIR/.venv"
VENV_PYTHON="$WORKER_DIR/.venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install "torch==$TORCH_VERSION" --index-url "https://download.pytorch.org/whl/$CUDA_WHEEL"
"$VENV_PYTHON" -m pip install -r "$WORKER_DIR/requirements.txt"
"$VENV_PYTHON" -m pip install -e "$WORKER_DIR"
"$VENV_PYTHON" -c "import torch; print('torch=', torch.__version__); print('build_cuda=', torch.version.cuda); print('cuda_available=', torch.cuda.is_available())"

