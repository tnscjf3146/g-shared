"""The key this machine signs on-chain work with, and how it is kept.

A supplier has two wallets and they are not the same thing:

* **Their own** -- MetaMask, the one that pays for jobs. It never comes near this
  program. There is no import, no seed phrase field, no place to paste it.
* **This machine's** -- created here, used to accept and settle the jobs this
  GPU runs. It is the ``provider`` the escrow contract will only take
  ``acceptJob`` from.

This module owns the second one.

**The private key is never written in the clear.** It is sealed with the same
Windows DPAPI the Worker token already uses (:mod:`gshared_worker.credentials`),
so a copied file is useless on another account or another machine. The address
sits next to it in the open, because an address is public by nature and the
Backend needs to be told it.

**A wallet that cannot be read is not replaced.** Making a fresh one would be
easy and would quietly abandon an identity that may hold a balance and may be
the ``provider`` on jobs already funded on chain. Every unreadable case ends in
:class:`WalletState.UNUSABLE` and a person deciding what to do.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from . import config as _config
from .credentials import protect_current_user, unprotect_current_user
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: Beside ``worker-config.json`` rather than inside it. The settings file is
#: something a supplier may open and edit; this is not, and widening the
#: settings contract with two keys nobody may type would say otherwise.
WALLET_FILE_NAME = "worker-wallet.json"

#: 0x and forty hex digits. Checked rather than assumed: this string is what
#: the Backend records as the provider, and a wrong one is a job accepted by
#: nobody.
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class WalletState(Enum):
    """Where this machine stands with its signing key."""

    #: Loaded, and the address matches the key. Ready to sign.
    PRESENT = "PRESENT"

    #: Nothing on disk. A wallet has never been made here.
    ABSENT = "ABSENT"

    #: Something is on disk that cannot be used -- sealed for another Windows
    #: account, half written, or naming an address the key does not produce.
    #: **Never replaced automatically.**
    UNUSABLE = "UNUSABLE"


class WalletError(RuntimeError):
    """This machine has no usable signing key, and none may be invented."""


@dataclass(frozen=True)
class WorkerWallet:
    """One signing identity: the address anyone may see, the key nobody may.

    ``private_key`` is ``repr=False`` and ``compare=False``. A dataclass that
    prints its own secret turns every log line, every assertion failure and
    every debugger into a disclosure, and it takes one ``%s`` to do it.
    """

    address: str
    private_key: str = field(repr=False, compare=False)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.short

    @property
    def short(self) -> str:
        """``0x1234...abcd`` -- enough to recognise, short enough to read."""
        return f"{self.address[:6]}...{self.address[-4:]}"


@dataclass(frozen=True)
class WalletLoad:
    state: WalletState
    wallet: WorkerWallet | None = None
    detail: str = ""

    @property
    def is_present(self) -> bool:
        return self.state is WalletState.PRESENT


class MakesAccounts(Protocol):
    """The whole of what this module needs from an Ethereum key library.

    A Protocol so everything above it -- creating, sealing, reloading, refusing
    -- runs and is tested without ``eth_account`` installed, and so the one
    place that imports it is :class:`EthAccounts`.
    """

    def create(self) -> tuple[str, str]:
        """A brand new ``(address, private key)``."""

    def address_of(self, private_key: str) -> str:
        """The address that key signs as."""


class EthAccounts:
    """The real thing, over ``eth_account`` -- imported inside the methods.

    ``eth_account`` arrives with ``web3``. Importing it at module scope would
    make every other part of the Worker, and every test of this file, depend on
    a package they do not use.
    """

    def create(self) -> tuple[str, str]:
        from eth_account import Account

        account = Account.create()
        # ``key`` is bytes; hex is what goes through DPAPI, which takes text.
        return account.address, account.key.hex()

    def address_of(self, private_key: str) -> str:
        from eth_account import Account

        return Account.from_key(private_key).address


def wallet_file_path(directory: Path | None = None) -> Path:
    # Through the module, so the directory is decided at call time -- the same
    # folder the settings file is read from.
    return (directory or _config.data_directory()) / WALLET_FILE_NAME


def load_wallet(
    directory: Path | None = None,
    *,
    unprotector: Callable[[str], str] | None = None,
    accounts: MakesAccounts | None = None,
) -> WalletLoad:
    """What this machine's signing key is, or why it cannot be used."""
    path = wallet_file_path(directory)
    if not path.is_file():
        return WalletLoad(WalletState.ABSENT, detail=f"{path} does not exist")

    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        return WalletLoad(
            WalletState.UNUSABLE,
            detail=f"{WALLET_FILE_NAME} could not be read: {error}")
    if not isinstance(values, dict):
        return WalletLoad(
            WalletState.UNUSABLE, detail=f"{WALLET_FILE_NAME} is not an object")

    address = values.get("address")
    sealed = values.get("privateKeyProtected")
    if not isinstance(address, str) or not _ADDRESS.match(address):
        return WalletLoad(
            WalletState.UNUSABLE,
            detail=f"{WALLET_FILE_NAME} does not name a usable address")
    if not isinstance(sealed, str) or not sealed.strip():
        return WalletLoad(
            WalletState.UNUSABLE,
            detail=f"{WALLET_FILE_NAME} has an address but no key beside it")

    try:
        private_key = (unprotector or unprotect_current_user)(sealed)
    except Exception:
        # What went wrong carries the ciphertext and the Windows error. Neither
        # belongs on a supplier's screen, and every cause has the same answer:
        # this key is not readable on this account.
        return WalletLoad(
            WalletState.UNUSABLE,
            detail="the signing key is sealed for a different Windows account "
                   "or machine and cannot be read here")

    try:
        derived = (accounts or EthAccounts()).address_of(private_key)
    except Exception:
        # Deliberately says nothing about the key it failed on.
        return WalletLoad(
            WalletState.UNUSABLE,
            detail="the stored signing key is not a usable Ethereum key")

    if derived.lower() != address.lower():
        # The file says one machine and the key signs as another. Which is
        # right is not ours to guess, and guessing wrong means accepting jobs
        # as somebody else or losing the identity that holds a balance.
        return WalletLoad(
            WalletState.UNUSABLE,
            detail="the stored signing key does not match the address recorded "
                   "beside it")

    return WalletLoad(
        WalletState.PRESENT, WorkerWallet(address=derived, private_key=private_key))


def create_wallet(
    directory: Path | None = None,
    *,
    protector: Callable[[str], str] | None = None,
    accounts: MakesAccounts | None = None,
) -> WorkerWallet:
    """Make this machine's one signing key and seal it.

    Writes through a temporary file and moves it into place, the same way the
    Worker credentials are written: an interrupted write must not leave an
    address with no key under it, because that reads as UNUSABLE for ever
    afterwards and no key can be recovered from it.
    """
    address, private_key = (accounts or EthAccounts()).create()
    if not _ADDRESS.match(address):
        raise WalletError("the key library produced an address we cannot use")

    sealed = (protector or protect_current_user)(private_key)
    path = wallet_file_path(directory)
    _write_atomically(path, {"address": address, "privateKeyProtected": sealed})

    _debug.info("wallet: created %s", _short(address))
    return WorkerWallet(address=address, private_key=private_key)


def _write_atomically(path: Path, values: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as opened:
            json.dump(values, opened, indent=2)
        os.replace(temporary, path)
    except BaseException:
        # A leftover temporary file is litter; a half-written wallet is worse.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def prepare_wallet(
    say: Callable[..., None],
    directory: Path | None = None,
    *,
    protector: Callable[[str], str] | None = None,
    unprotector: Callable[[str], str] | None = None,
    accounts: MakesAccounts | None = None,
) -> WorkerWallet:
    """The one this machine already has, or a first one. Never a replacement.

    Raises :class:`WalletError` when a wallet is there and cannot be used.
    That is the whole point of the function: the easy thing to do with an
    unreadable key file is to write a new one over it, and that silently throws
    away an on-chain identity.
    """
    loaded = load_wallet(directory, unprotector=unprotector, accounts=accounts)

    if loaded.state is WalletState.UNUSABLE:
        _debug.info("wallet: unusable -- %s", loaded.detail)
        raise WalletError(loaded.detail)

    if loaded.is_present:
        _debug.info("wallet: loaded %s", _short(loaded.wallet.address))
        say(f"  Worker 지갑 준비 완료: {loaded.wallet.short}")
        return loaded.wallet

    say("  이 PC의 지갑을 처음 만드는 중...")
    wallet = create_wallet(directory, protector=protector, accounts=accounts)
    say(f"  Worker 지갑 준비 완료: {wallet.short}")
    return wallet


def _short(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


class OperatorPlan(Enum):
    """What to do about a Worker whose operator address may need attaching."""

    #: The Backend already knows this machine's address. Nothing to send.
    ALREADY_REGISTERED = "ALREADY_REGISTERED"
    #: The Backend has no address for this Worker. Ours can be attached.
    REGISTER = "REGISTER"
    #: The Backend knows a **different** address. Not overwritten -- see below.
    BLOCKED_MISMATCH = "BLOCKED_MISMATCH"
    #: A job is in hand. An operator swap now would strand it.
    BLOCKED_ACTIVE_JOB = "BLOCKED_ACTIVE_JOB"


@dataclass(frozen=True)
class OperatorDecision:
    plan: OperatorPlan
    detail: str

    @property
    def may_register(self) -> bool:
        return self.plan is OperatorPlan.REGISTER


def plan_operator_migration(
    local_address: str,
    backend_address: str | None,
    *,
    holding_a_job: bool,
) -> OperatorDecision:
    """Whether this machine's address may be attached to an existing Worker.

    Installations that predate operator keys have a workerId and a token but no
    signing address, and they need one before they can be given work. Attaching
    it is a small thing; attaching it **over** something is not.

    Two refusals, both deliberate.

    **A different address already registered.** Silently replacing it would
    orphan every job the old key accepted: ``submitResult`` and ``abortJob``
    check ``msg.sender == job.operator``, so those jobs could then be finished
    by nobody, and their deposits would sit until the execution timeout expired.
    It also usually means something unexpected -- two installations sharing one
    workerId, or a restored backup -- and none of those are improved by this
    program picking a winner.

    **A job in hand.** Even with no address registered, rotating while holding
    work is the same trap one step earlier.
    """
    if holding_a_job:
        return OperatorDecision(
            OperatorPlan.BLOCKED_ACTIVE_JOB,
            "this Worker is holding a job; its operator address is not "
            "changed while work is in hand")
    if not backend_address:
        return OperatorDecision(
            OperatorPlan.REGISTER,
            "this Worker has no operator address registered yet")
    if backend_address.lower() == local_address.lower():
        return OperatorDecision(
            OperatorPlan.ALREADY_REGISTERED,
            "the registered operator address is this machine's")
    return OperatorDecision(
        OperatorPlan.BLOCKED_MISMATCH,
        "a different operator address is registered for this Worker; it is "
        "not overwritten, because jobs accepted with the other key could then "
        "be finished by nobody")


def check_operator_matches(wallet: WorkerWallet, registered_address: str) -> None:
    """The key on this PC is the one the Backend has on file, or stop.

    Called before accepting, as the last of the key checks. The earlier ones
    (:func:`load_wallet`) prove the key is readable and internally consistent;
    this one proves it is the **right** key. A Worker signing with a key the
    provider never approved produces transactions the contract rejects, and a
    string of refusals that look like somebody else's fault.
    """
    if not registered_address:
        raise WalletError(
            "no operator address is registered for this Worker, so the key on "
            "this PC cannot be confirmed as the right one")
    if wallet.address.lower() != registered_address.lower():
        raise WalletError(
            "the signing key on this PC is not the operator address "
            "registered for this Worker")
