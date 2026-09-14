# -*- mode: python ; coding: utf-8 -*-
#
# G-shared.exe -- the Worker a GPU supplier runs on their own Windows PC.
#
# Build with packaging\build.ps1, not by hand: it freezes from a fresh virtual
# environment holding only requirements-build.txt, so nothing on the build
# machine leaks in.
#
# What is in the bundle: Python, gshared_worker, websockets, web3/eth-account,
# tkinter, and one data file -- the canonical escrow ABI, byte for byte.
#
# What is deliberately NOT in it, and never may be:
#   * worker-config.json, worker-wallet.json, .env, any token or key. Those
#     are made on the supplier's PC and sealed there with DPAPI.
#   * the training runtime (torch, transformers...). Training runs in the
#     Docker image, pulled by digest when a job arrives.
#   * the image itself, model weights, datasets, tests, fixtures, git data.
#
# Two executables from one recipe: G-shared.exe, and selfcheck.exe, which
# build.ps1 runs against the same Analysis to prove the bundle is complete and
# then discards. selfcheck.exe is never shipped.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

WORKER = Path(SPECPATH).resolve().parent          # system/worker
REPO = WORKER.parent.parent                       # repository root
ABI = REPO / "blockchain" / "abi" / "GPUJobEscrow.json"

if not ABI.is_file():
    raise SystemExit(f"escrow ABI not found at {ABI}")

# Every Worker module, including the job-execution ones reached only when work
# arrives -- a supplier's first job is the wrong moment to find one missing.
# The spec runs in PyInstaller's own process, which cannot import the Worker
# until its folder is on the path; without this the list is silently empty.
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))
WORKER_MODULES = collect_submodules("gshared_worker")
if len(WORKER_MODULES) < 40:
    raise SystemExit(f"only {len(WORKER_MODULES)} gshared_worker modules found")

# Imported inside functions in the Worker, so named here rather than left to
# static analysis finding them.
HIDDEN = WORKER_MODULES + [
    "websockets.sync.client",
    "eth_account",
    "web3",
    "web3.exceptions",
    "tkinter",
    "tkinter.ttk",
]

# Worker code never needs these. Listed so a build machine that has them
# installed still cannot put them in. benchmark_program.py names torch, but
# that code runs inside the runtime container, never in this process.
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "transformers", "peft", "bitsandbytes",
    "accelerate", "datasets", "xgboost", "sklearn", "scipy", "pandas", "numpy",
    "matplotlib", "IPython", "jupyter", "notebook",
    "pytest", "_pytest", "unittest.mock", "tests",
    "setuptools", "pip", "wheel",
]

DATAS = [(str(ABI), "blockchain/abi")]

# Package metadata (dist-info: names and versions, no code, nothing secret) for
# the runtime dependencies and everything they pull in. web3, py_ecc, toolz and
# pydantic read their own version with importlib.metadata while being imported;
# without these files `import eth_account` fails inside the bundle and the
# Worker cannot make its signing key. Found by selfcheck.exe, not by the build.
RUNTIME_DISTRIBUTIONS = ["websockets", "web3", "eth-account"]
METADATA = []
for _dist in RUNTIME_DISTRIBUTIONS:
    METADATA += copy_metadata(_dist, recursive=True)


def analysis(script, extra=()):
    return Analysis(
        [str(script)],
        pathex=[str(WORKER)],
        binaries=[],
        datas=DATAS + METADATA + list(extra),
        hiddenimports=HIDDEN,
        hookspath=[],
        runtime_hooks=[],
        excludes=EXCLUDES,
        noarchive=False,
        optimize=0,
    )


def onefile(a, name):
    pyz = PYZ(a.pure)
    return EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        # The Worker speaks to the supplier on a console and holds a failed
        # run on screen; the sign-in window is opened from there.
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


worker = analysis(WORKER / "gshared_worker" / "__main__.py")
onefile(worker, "G-shared")

if os.environ.get("GSHARED_BUILD_SELFCHECK") == "1":
    # The module list is handed to selfcheck.exe as a file, because a frozen
    # package cannot be walked to discover its own submodules.
    modules = Path(workpath) / "selfcheck-modules.txt"
    modules.write_text(chr(10).join(WORKER_MODULES), encoding="utf-8")
    check = analysis(WORKER / "packaging" / "selfcheck.py",
                     extra=[(str(modules), ".")])
    onefile(check, "selfcheck")
