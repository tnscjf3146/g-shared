# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


ffi_dll = Path(sys.base_prefix) / "Library" / "bin" / "ffi-8.dll"
ffi_binaries = [(str(ffi_dll), ".")] if ffi_dll.is_file() else []

a = Analysis(
    ["gpu_worker/agent_entry.py"],
    pathex=["."],
    binaries=ffi_binaries,
    datas=[],
    hiddenimports=["pynvml", "websockets"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "sentence_transformers"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gpu-worker-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="gpu-worker-agent",
)
