param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$workerRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = (Resolve-Path (Join-Path $workerRoot $Python)).Path

Push-Location $workerRoot
try {
    $basePrefix = (& $pythonPath -c "import sys; print(sys.base_prefix)").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Unable to resolve Python base prefix" }
    $env:Path = "$basePrefix;$basePrefix\Library\bin;$env:Path"
    & $pythonPath -m pip install ".[agent-build]"
    if ($LASTEXITCODE -ne 0) { throw "Agent build dependency installation failed" }
    & $pythonPath -m PyInstaller --noconfirm --clean .\gpu-worker-agent.spec
    if ($LASTEXITCODE -ne 0) { throw "Agent build failed" }
    Copy-Item .\worker-config.example.json .\dist\gpu-worker-agent\ -Force
    & .\dist\gpu-worker-agent\gpu-worker-agent.exe --help
    if ($LASTEXITCODE -ne 0) { throw "Agent smoke test failed" }
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $noArgs = & .\dist\gpu-worker-agent\gpu-worker-agent.exe 2>&1 | Out-String
    $noArgsExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($noArgsExitCode -ne 2) { throw "Agent without configuration must exit with code 2" }
    if ($noArgs -notmatch "worker-config\.json") {
        throw "Agent without configuration must name worker-config.json"
    }
} finally {
    Pop-Location
}
