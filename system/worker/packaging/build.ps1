<#
Build G-shared.exe from a clean virtual environment and prove the bundle is whole.

  powershell -ExecutionPolicy Bypass -File system\worker\packaging\build.ps1

1. Makes a fresh venv under packaging\build\venv holding only
   requirements-build.txt, so nothing installed on this machine leaks in.
2. Freezes G-shared.exe and selfcheck.exe from G-shared.spec.
3. Runs selfcheck.exe (imports, escrow ABI, eth_account key + DPAPI round trip,
   no torch/pandas/numpy/pytest in the bundle). Any FAIL stops the build.
4. Deletes selfcheck.exe -- it is never shipped -- and prints the size and
   SHA-256 of dist\G-shared.exe.

build\ and dist\ are git-ignored. Nothing here reads a token, a key or .env.
#>
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Build = Join-Path $Here "build"
$Dist  = Join-Path $Here "dist"
$Venv  = Join-Path $Build "venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

Write-Host "== clean venv"
if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
& $Python -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
& $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Here "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

Write-Host "== freeze"
$env:GSHARED_BUILD_SELFCHECK = "1"
try {
    & $VenvPython -m PyInstaller --noconfirm --clean `
        --distpath $Dist --workpath (Join-Path $Build "work") `
        (Join-Path $Here "G-shared.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
} finally {
    Remove-Item Env:\GSHARED_BUILD_SELFCHECK -ErrorAction SilentlyContinue
}

Write-Host "== selfcheck"
$Check = Join-Path $Dist "selfcheck.exe"
& $Check
$CheckCode = $LASTEXITCODE
Remove-Item -Force $Check
if ($CheckCode -ne 0) { throw "selfcheck failed ($CheckCode)" }

$Exe = Join-Path $Dist "G-shared.exe"
$Size = (Get-Item $Exe).Length
$Hash = (Get-FileHash -Algorithm SHA256 $Exe).Hash.ToLower()
Write-Host ("== built {0}  {1:N1} MB  sha256 {2}" -f $Exe, ($Size / 1MB), $Hash)
