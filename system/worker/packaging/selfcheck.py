"""Build verification only: run inside a frozen copy, prove the bundle is whole.

Not part of the Worker and not shipped to suppliers. ``build.ps1`` freezes this
with exactly the same Analysis as ``G-shared.exe`` (see ``G-shared.spec``) and
runs it, so a module or data file the Worker only reaches later -- signing a
transaction, opening the Backend connection, the sign-in window -- is found
missing at build time instead of on a supplier's PC halfway through a job.

Every check prints one ``PASS``/``FAIL`` line. Nothing secret is printed: the
throwaway key made here is sealed, unsealed, compared and discarded.
"""

from __future__ import annotations

import sys


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        try:
            detail = fn() or ""
            results.append((name, True, str(detail)))
        except Exception as error:  # noqa: BLE001 -- report every failure
            results.append((name, False, f"{type(error).__name__}: {error}"))

    check("frozen", lambda: _require(getattr(sys, "frozen", False), "not frozen"))

    def worker_modules():
        import importlib
        from pathlib import Path

        # Written by G-shared.spec at build time; a frozen package cannot be
        # walked for its submodules.
        listing = Path(getattr(sys, "_MEIPASS", ".")) / "selfcheck-modules.txt"
        names = [n for n in listing.read_text(encoding="utf-8").splitlines() if n]
        _require(len(names) > 40, f"only {len(names)} modules listed")
        for name in names:
            importlib.import_module(name)
        return f"{len(names)} modules"

    check("gshared_worker imports", worker_modules)

    def lazy_imports():
        # Imported inside functions in the Worker; resolved here so a missing
        # hidden import shows now.
        import websockets.sync.client  # noqa: F401
        from eth_account import Account  # noqa: F401
        from web3 import HTTPProvider, Web3  # noqa: F401
        from web3.exceptions import TimeExhausted  # noqa: F401
        import tkinter  # noqa: F401
        from tkinter import ttk  # noqa: F401
        from importlib.metadata import version

        # The lookups that failed before the metadata was bundled.
        seen = {name: version(name) for name in ("web3", "py_ecc", "eth-account",
                                                 "websockets")}
        return f"websockets, eth_account, web3, tkinter; {seen}"

    check("lazy runtime imports", lazy_imports)

    def nothing_heavy():
        heavy = [m for m in ("torch", "transformers", "xgboost", "pandas",
                             "numpy", "sklearn", "pytest", "tests")
                 if _importable(m)]
        _require(not heavy, f"bundled but not needed: {heavy}")
        return "no torch/transformers/xgboost/pandas/numpy/sklearn/pytest/tests"

    check("no heavy dependencies", nothing_heavy)

    def abi():
        from gshared_worker.escrow_abi import EscrowArtifact

        artifact = EscrowArtifact.load()
        _require(artifact.contract_name == "GPUJobEscrow", artifact.contract_name)
        _require(str(artifact.source).startswith(getattr(sys, "_MEIPASS", "?")),
                 f"read from outside the bundle: {artifact.source}")
        return f"GPUJobEscrow from bundle, {len(artifact.abi)} entries"

    check("escrow ABI", abi)

    def runtime_image():
        from gshared_worker.runtime_image import (
            CANONICAL_RUNTIME_DIGEST, CANONICAL_RUNTIME_REFERENCE,
            CANONICAL_RUNTIME_TAG, PLACEHOLDER_DIGEST, ImageRefused, check_image)

        image = check_image(CANONICAL_RUNTIME_REFERENCE, CANONICAL_RUNTIME_DIGEST)
        _require(image.reference == CANONICAL_RUNTIME_REFERENCE, image.reference)
        for refused, digest in (
                (f"{image.repository}@{PLACEHOLDER_DIGEST}", PLACEHOLDER_DIGEST),
                (f"{image.repository}:{CANONICAL_RUNTIME_TAG}", CANONICAL_RUNTIME_DIGEST)):
            try:
                check_image(refused, digest)
            except ImageRefused:
                continue
            raise AssertionError(f"accepted {refused}")
        return (f"{image.repository}:{CANONICAL_RUNTIME_TAG} pinned to "
                f"{CANONICAL_RUNTIME_DIGEST[:19]}...; placeholder and tag refused")

    check("runtime image", runtime_image)

    def chain_config():
        import re

        from gshared_worker.config import (
            DEFAULT_CHAIN_ID, DEFAULT_ESCROW_ADDRESS, DEFAULT_ESCROW_RPC_URL)
        from gshared_worker.escrow import SSAFY_CHAIN_ID

        _require(DEFAULT_CHAIN_ID == SSAFY_CHAIN_ID == 31221,
                 f"chain {DEFAULT_CHAIN_ID} / {SSAFY_CHAIN_ID}")
        _require(re.fullmatch(r"0x[0-9a-fA-F]{40}", DEFAULT_ESCROW_ADDRESS or ""),
                 f"escrow address {DEFAULT_ESCROW_ADDRESS!r}")
        _require(DEFAULT_ESCROW_RPC_URL.startswith("https://"), DEFAULT_ESCROW_RPC_URL)
        return (f"chain {DEFAULT_CHAIN_ID}, escrow {DEFAULT_ESCROW_ADDRESS}, "
                f"rpc {DEFAULT_ESCROW_RPC_URL}")

    check("chain config", chain_config)

    def wallet_and_dpapi():
        from gshared_worker.credentials import (
            protect_current_user, unprotect_current_user)
        from gshared_worker.worker_wallet import EthAccounts

        accounts = EthAccounts()
        address, key = accounts.create()
        _require(accounts.address_of(key) == address, "address mismatch")
        sealed = protect_current_user(key)
        _require(sealed != key and key not in sealed, "not sealed")
        _require(unprotect_current_user(sealed) == key, "DPAPI round trip")
        return "eth_account key made, DPAPI sealed and unsealed"

    check("wallet + DPAPI", wallet_and_dpapi)

    def data_directory():
        from gshared_worker.config import data_directory, worker_directory

        where = data_directory()
        return f"program={worker_directory()} data={where}"

    check("data directory", data_directory)

    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    failed = [name for name, ok, _ in results if not ok]
    print("SELFCHECK", "PASS" if not failed else f"FAIL {failed}")
    return 0 if not failed else 1


def _importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
