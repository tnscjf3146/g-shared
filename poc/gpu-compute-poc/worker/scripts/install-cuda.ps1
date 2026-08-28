param(
    [string]$Python = "python",
    [string]$TorchVersion = "2.12.1",
    [ValidateSet("cu126", "cu130", "cu132")]
    [string]$CudaWheel = "cu126"
)

$ErrorActionPreference = "Stop"

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ is required')"
& $Python -m venv .venv
$VenvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install "torch==$TorchVersion" --index-url "https://download.pytorch.org/whl/$CudaWheel"
& $VenvPython -m pip install -r (Join-Path $PSScriptRoot "..\requirements.txt")
& $VenvPython -m pip install -e (Join-Path $PSScriptRoot "..")
& $VenvPython -c "import torch; print('torch=', torch.__version__); print('build_cuda=', torch.version.cuda); print('cuda_available=', torch.cuda.is_available())"

