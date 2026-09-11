"""Accepting a funded job on chain, and giving one back.

This machine signs with an **operator** key. It is not paid. The money goes to
the **provider** -- the person who owns this GPU, whose MetaMask address sits
in ``users.wallet_address``. The contract keeps them apart on purpose::

    provider   users.wallet_address        정산 주소. 대금이 여기로 간다
    operator   workers.operator_address    온체인 호출 권한. 대금과 무관

The previous version of this file checked ``job.provider == our own wallet``.
That is the wrong question and it can never be true in the finished design: a
Worker's key is an operator key, and a provider is a human. The right question
is ``isOperator(provider, us)`` -- did that human approve this machine -- and
it is asked on chain, immediately before signing.

**Nothing in here is taken from a JOB_ASSIGN except the job's own terms.** The
contract address, the chain id and the RPC endpoint are configuration. A
Worker that signed against whatever address a message named would be a signing
service for anyone who reached the socket.

**The ABI is not in this file.** It is loaded from the Blockchain module's
generated artifact -- see :mod:`gshared_worker.escrow_abi` for why that matters
more than it sounds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol

from .escrow_abi import EscrowArtifact, check_contract_matches
from .job_rejection import JobRefused, RejectCode
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: The SSAFY chain. Every acceptance checks the node actually answers with it.
SSAFY_CHAIN_ID = 31221

#: GCoin has 18 decimals. Prices travel as decimal strings of the smallest
#: unit and are compared as integers -- never as floats, which lose exactness
#: somewhere above nine tokens.
WEI_PER_TOKEN = 10 ** 18

RECEIPT_TIMEOUT_SECONDS = 120


class JobState(IntEnum):
    """``GPUJobEscrow.JobState``, matching the contract's own numbering.

    Five values. An earlier build of this package had seven, with ``FUNDED``
    at 2, taken from a contract revision that was later simplified. Under that
    numbering a 2 read back as FUNDED when it now means **ASSIGNED** -- so a
    job somebody else had already taken looked like one waiting to be funded.

    :func:`~gshared_worker.escrow_abi.check_contract_matches` compares this
    enum against the artifact's ``stateEnum`` so the next renumbering fails a
    test instead of a transaction.
    """

    NONE = 0
    FUNDED = 1
    ASSIGNED = 2
    COMPLETED = 3
    CANCELLED = 4

    @classmethod
    def as_mapping(cls) -> dict[int, str]:
        return {int(state): state.name for state in cls}


@dataclass(frozen=True)
class EscrowJob:
    """One row of ``jobs(jobId)``, named.

    Seven fields, in the contract's order. The names exist so that no code
    below reaches for a tuple index -- the last redesign inserted ``operator``
    in the middle, which turned every ``row[4]`` from a provider into a price
    without changing a line of code or raising anything.
    """

    client: str
    state: JobState
    deadline: int
    provider: str
    operator: str
    price_wei: int
    result_hash: str

    @property
    def exists(self) -> bool:
        """A job that was never created reads back as all zeroes."""
        return self.state is not JobState.NONE

    @classmethod
    def from_row(cls, row: Any) -> "EscrowJob":
        """Decode one ``jobs()`` return. The only place positions are used."""
        if len(row) != 7:
            raise EscrowUnavailable(
                f"jobs() returned {len(row)} values, expected 7")
        client, state, deadline, provider, operator, price, result_hash = row
        return cls(
            client=str(client),
            state=JobState(int(state)),
            deadline=int(deadline),
            provider=str(provider),
            operator=str(operator),
            price_wei=int(price),
            result_hash=_as_hex(result_hash),
        )


@dataclass(frozen=True)
class FundedEvent:
    """A ``JobFunded`` log, which is the only place ``externalRef`` lives.

    It is deliberately not a field of the job struct -- the contract keeps
    only settlement state on chain -- so checking that a JOB_ASSIGN refers to
    the job the Backend thinks it does means reading this event.
    """

    escrow_job_id: int
    external_ref: str
    provider: str
    client: str
    price_wei: int
    deadline: int


class EscrowUnavailable(RuntimeError):
    """The chain could not be reached, or is not the one we were built for.

    Not a job problem. The Backend connection is unaffected and the heartbeat
    goes on -- an RPC that is down says nothing about a WebSocket that is up.
    """


class ReadsEscrow(Protocol):
    """The whole of what acceptance needs from a chain.

    A Protocol so every check below is exercised without a node, and so the
    one place that imports ``web3`` is :class:`Web3Escrow`.
    """

    def chain_id(self) -> int: ...

    def has_contract_code(self) -> bool:
        """Whether the configured address actually holds a contract."""

    def read_job(self, escrow_job_id: int) -> EscrowJob: ...

    def find_funded_event(self, escrow_job_id: int) -> FundedEvent | None:
        """The ``JobFunded`` log for this job, or None if there is not exactly one."""

    def is_operator(self, provider: str, operator: str) -> bool:
        """``isOperator(provider, operator)`` -- did that provider approve us."""

    def now(self) -> int:
        """The chain's clock in seconds, from the latest block."""

    def send_accept(self, escrow_job_id: int) -> str:
        """Sign and broadcast ``acceptJob``. Returns the transaction hash."""

    def send_submit_result(self, escrow_job_id: int, result_hash: str) -> str:
        """``submitResult(jobId, bytes32)``. Requires ``msg.sender ==
        job.operator``, like the other two."""

    def send_abort(self, escrow_job_id: int) -> str:
        """Sign and broadcast ``abortJob``. Returns the transaction hash."""

    def receipt_status(self, transaction_hash: str, timeout: float) -> int | None:
        """1 for success, 0 for a reverted transaction, None if still unknown."""


@dataclass(frozen=True)
class EscrowSettings:
    """Where the escrow is, and what must be true of it before we sign.

    **None of this comes from a JOB_ASSIGN.** A message that could name the
    contract to send to could name any contract.
    """

    rpc_url: str
    escrow_address: str
    chain_id: int = SSAFY_CHAIN_ID

    @property
    def is_configured(self) -> bool:
        return bool(self.rpc_url and self.escrow_address)


def check_chain(chain: ReadsEscrow, settings: EscrowSettings) -> None:
    """Refuse the wrong chain, or an address with nothing on it.

    Done once per acceptance rather than once at startup: a node can be swapped
    under a long-running Worker, and the answer that matters is the one true at
    the moment of signing.
    """
    try:
        actual = chain.chain_id()
    except Exception as error:
        raise JobRefused(
            RejectCode.BLOCKCHAIN_RPC_FAILED,
            f"the chain could not be reached ({type(error).__name__})") from None
    if actual != settings.chain_id:
        raise EscrowUnavailable(
            f"connected to chain {actual}, expected {settings.chain_id}")
    try:
        present = chain.has_contract_code()
    except Exception as error:
        raise JobRefused(
            RejectCode.BLOCKCHAIN_RPC_FAILED,
            f"the escrow address could not be read ({type(error).__name__})") from None
    if not present:
        raise EscrowUnavailable(
            "no contract is deployed at the configured escrow address")


def check_job_matches(
    job: EscrowJob,
    funded: FundedEvent | None,
    terms: Any,
    *,
    operator_address: str,
) -> bool:
    """Compare the chain against what the Backend claimed. True to send.

    ``terms`` is the escrow half of a JOB_ASSIGN
    (:class:`~gshared_worker.worker_job.EscrowTerms`). Every field of it is
    compared against the chain, because **the on-chain price is not enforced
    by the contract** -- the client writes it into the funding transaction, so
    a client could fund one token and name a machine worth far more. The
    contract's own documentation says so plainly:

        가격이 온체인에서 강제되지 않습니다. 공급자 노드의 대조가 유일한
        방어선입니다.

    Returns False when the job is already assigned to this operator, which is
    a success with nothing left to send.
    """
    escrow_job_id = terms.escrow_job_id

    if not job.exists:
        raise JobRefused(
            RejectCode.ESCROW_JOB_NOT_FOUND,
            f"escrow job {escrow_job_id} does not exist on chain")

    # externalRef lives in the event, not the struct. Checked before anything
    # else about the job, because it is what ties this on-chain job to the
    # Backend job the message is about; without it the rest compares the right
    # numbers against possibly the wrong job.
    if funded is None:
        raise JobRefused(
            RejectCode.EXTERNAL_REF_MISMATCH,
            f"no single JobFunded event was found for escrow job "
            f"{escrow_job_id}, so its externalRef could not be confirmed")
    if not _same_hex(funded.external_ref, terms.external_ref):
        raise JobRefused(
            RejectCode.EXTERNAL_REF_MISMATCH,
            f"escrow job {escrow_job_id} was funded for a different "
            f"externalRef than this assignment names")

    if not _same_address(job.client, terms.client_wallet):
        raise JobRefused(
            RejectCode.CLIENT_MISMATCH,
            f"escrow job {escrow_job_id} was funded by a different client "
            f"than this assignment names")

    if not _same_address(job.provider, terms.provider_wallet):
        raise JobRefused(
            RejectCode.PROVIDER_MISMATCH,
            f"escrow job {escrow_job_id} pays a different provider than this "
            f"assignment names")

    if job.price_wei != terms.price_wei:
        # Integers, exactly. This is the check the contract cannot make.
        raise JobRefused(
            RejectCode.PRICE_MISMATCH,
            f"escrow job {escrow_job_id} is funded for {job.price_wei} wei, "
            f"not the {terms.price_wei} this assignment names")

    if job.state is JobState.ASSIGNED:
        if _same_address(job.operator, operator_address):
            # Already ours. A second transaction would revert on "not FUNDED".
            _debug.info("escrow: job %s is already accepted by this machine",
                        escrow_job_id)
            return False
        raise JobRefused(
            RejectCode.INVALID_ESCROW_STATE,
            f"escrow job {escrow_job_id} is already assigned to another operator")

    if job.state is not JobState.FUNDED:
        raise JobRefused(
            RejectCode.INVALID_ESCROW_STATE,
            f"escrow job {escrow_job_id} is {job.state.name}, not FUNDED")

    return True


def check_deadline(job: EscrowJob, chain_now: int, escrow_job_id: int) -> None:
    """The accept deadline, against the **chain's** clock.

    Deliberately not this machine's clock. A Windows PC whose time is a minute
    fast would refuse jobs the contract would have accepted, and the supplier
    would see work quietly passing them by with nothing in the log to explain
    it. The contract compares the same way -- ``block.timestamp > deadline``
    reverts, and the deadline second itself is still allowed -- so no margin is
    invented here.
    """
    if chain_now > job.deadline:
        raise JobRefused(
            RejectCode.ACCEPT_DEADLINE_EXPIRED,
            f"escrow job {escrow_job_id} passed its accept deadline")


def check_operator_approved(
    chain: ReadsEscrow, provider_wallet: str, operator_address: str,
) -> None:
    """``isOperator(provider, us)``, read from the chain before signing.

    The Backend is expected to filter unapproved machines out of scheduling,
    and that check is not a substitute for this one. Theirs prevents a wasted
    assignment; this one is the last thing standing between a bug and a
    signature. They also answer different questions -- the Backend's copy can
    be stale, and this one is read in the same second as the transaction.
    """
    try:
        approved = chain.is_operator(provider_wallet, operator_address)
    except Exception as error:
        raise JobRefused(
            RejectCode.BLOCKCHAIN_RPC_FAILED,
            f"isOperator could not be read ({type(error).__name__})") from None
    if not approved:
        raise JobRefused(
            RejectCode.OPERATOR_NOT_APPROVED,
            "the provider for this job has not approved this machine as an "
            "operator")


class AcceptOutcome(IntEnum):
    """How an acceptance ended. The third one is the one that matters."""

    #: On chain, ours, confirmed by reading the job back.
    ACCEPTED = 0

    #: A settled no. The reservation may be given up.
    REFUSED = 1

    #: We do not know. A transaction may be in flight, or the node stopped
    #: answering mid-way. **The reservation is kept** -- accepting something
    #: else while a transaction of ours may still land would have this machine
    #: owing two jobs.
    UNCERTAIN = 2


@dataclass(frozen=True)
class AcceptResult:
    outcome: AcceptOutcome
    detail: str
    transaction_hash: str | None = None
    code: RejectCode | None = None
    #: What the chain last said this job was, when it was read. Carried because
    #: a refusal is not one thing: a job that is FUNDED was never ours and can
    #: be let go, while one that is COMPLETED is a genuine disagreement with
    #: the Backend and must not be quietly tidied away. The caller needs to
    #: tell those apart and the outcome alone does not.
    state: "JobState | None" = None

    @property
    def accepted(self) -> bool:
        return self.outcome is AcceptOutcome.ACCEPTED

    @property
    def transient(self) -> bool:
        """Whether trying again could plausibly give a different answer."""
        return self.code is not None and self.code in _TRANSIENT_CODES


_TRANSIENT_CODES = frozenset({RejectCode.BLOCKCHAIN_RPC_FAILED})


def accept_escrow_job(
    chain: ReadsEscrow,
    settings: EscrowSettings,
    terms: Any,
    *,
    operator_address: str,
    receipt_timeout: float = RECEIPT_TIMEOUT_SECONDS,
) -> AcceptResult:
    """Check everything, sign if it all holds, and confirm it landed.

    The order is chosen so that nothing expensive happens before something
    cheap has ruled it out, and so that **no transaction is ever signed on an
    unverified fact**. Reads first, signature last.

    Confirmation is a second read rather than the receipt alone. A receipt says
    a transaction succeeded; reading the job back says this machine is the
    operator on a job that is now ASSIGNED, which is what has to be true before
    the Backend is told anything.
    """
    escrow_job_id = terms.escrow_job_id
    try:
        check_chain(chain, settings)
        job = chain.read_job(escrow_job_id)
        funded = chain.find_funded_event(escrow_job_id)
        must_send = check_job_matches(
            job, funded, terms, operator_address=operator_address)
        if must_send:
            check_deadline(job, chain.now(), escrow_job_id)
            check_operator_approved(
                chain, terms.provider_wallet, operator_address)
    except JobRefused as refused:
        _debug.info("escrow: refusing job %s -- %s (%s)",
                    escrow_job_id, refused.detail, refused.code.value)
        return AcceptResult(
            AcceptOutcome.REFUSED, refused.detail, code=refused.code)
    except EscrowUnavailable as unavailable:
        # Nothing was sent, so nothing is in flight -- but the job is still
        # ours to take when the chain comes back, so this is not a refusal.
        _debug.info("escrow: chain unavailable -- %s", unavailable)
        return AcceptResult(
            AcceptOutcome.UNCERTAIN, str(unavailable),
            code=RejectCode.BLOCKCHAIN_RPC_FAILED)
    except Exception as error:
        _debug.info("escrow: pre-accept check failed (%s)", type(error).__name__)
        return AcceptResult(
            AcceptOutcome.UNCERTAIN,
            f"the job could not be checked ({type(error).__name__})",
            code=RejectCode.BLOCKCHAIN_RPC_FAILED)

    if not must_send:
        return AcceptResult(
            AcceptOutcome.ACCEPTED, "this machine had already accepted it")

    return _send_and_confirm(
        chain, escrow_job_id, operator_address,
        send=chain.send_accept, receipt_timeout=receipt_timeout,
        wanted=JobState.ASSIGNED, what="accept")


def abort_escrow_job(
    chain: ReadsEscrow,
    settings: EscrowSettings,
    *,
    escrow_job_id: int,
    operator_address: str,
    receipt_timeout: float = RECEIPT_TIMEOUT_SECONDS,
) -> AcceptResult:
    """Give a job back, refunding the client immediately.

    A capability, not a policy. Nothing in the accept path calls this: a job
    that was accepted and is waiting for a GPU runtime that does not exist yet
    is not a failure, and aborting it automatically would give work away.

    It exists now because the alternative during development is worse. A test
    job accepted against a local chain holds the client's deposit for the full
    execution timeout -- twenty-four hours -- before ``settleTimeout`` can
    refund it. ``abortJob`` returns it in one transaction, and the signing code
    is the same code ``acceptJob`` uses.

    The precondition is exact and is checked on chain: ASSIGNED, **and this
    machine is the operator**. Another machine's job is never given back --
    ``submitResult`` and ``abortJob`` both require ``msg.sender ==
    job.operator``, so a job accepted with a different key can only be finished
    by that key.
    """
    try:
        check_chain(chain, settings)
        job = chain.read_job(escrow_job_id)
    except JobRefused as refused:
        return AcceptResult(
            AcceptOutcome.REFUSED, refused.detail, code=refused.code)
    except EscrowUnavailable as unavailable:
        return AcceptResult(
            AcceptOutcome.UNCERTAIN, str(unavailable),
            code=RejectCode.BLOCKCHAIN_RPC_FAILED)
    except Exception as error:
        return AcceptResult(
            AcceptOutcome.UNCERTAIN,
            f"the job could not be read ({type(error).__name__})",
            code=RejectCode.BLOCKCHAIN_RPC_FAILED)

    if job.state is JobState.CANCELLED:
        # Already given back. Reconciled, not refused -- and **no second
        # transaction**: the chain is already in the state we wanted.
        return AcceptResult(
            AcceptOutcome.ACCEPTED, "the job was already cancelled",
            state=job.state)
    if job.state is not JobState.ASSIGNED:
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"escrow job {escrow_job_id} is {job.state.name}, not ASSIGNED",
            code=RejectCode.INVALID_ESCROW_STATE, state=job.state)
    if not _same_address(job.operator, operator_address):
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"escrow job {escrow_job_id} was accepted by another operator and "
            "cannot be aborted by this machine",
            code=RejectCode.OPERATOR_ADDRESS_MISMATCH, state=job.state)

    return _send_and_confirm(
        chain, escrow_job_id, operator_address,
        send=chain.send_abort, receipt_timeout=receipt_timeout,
        wanted=JobState.CANCELLED, what="abort")


def submit_escrow_result(
    chain: ReadsEscrow,
    settings: EscrowSettings,
    *,
    escrow_job_id: int,
    operator_address: str,
    result_hash: str,
    receipt_timeout: float = RECEIPT_TIMEOUT_SECONDS,
) -> AcceptResult:
    """Record the result on chain and release the client's deposit.

    **Called only after the Backend has verified the uploaded object.** The
    hash written here is the hash of bytes that are already in storage; doing
    it the other way round would put a number on chain that nothing can be
    checked against, and the contract has no way to take it back.

    The preconditions are re-read rather than remembered. Between accepting
    the job and finishing it there were minutes to hours of training, and what
    was true then is a claim about the past: the state may have moved, and the
    operator may no longer be us.

    Reaching COMPLETED with this exact hash already -- because an earlier
    attempt landed after its receipt was lost -- is a **success, with no
    second transaction**. Reaching COMPLETED with a different hash is not
    something to resolve by sending another one; it is reported and the job
    stops here.
    """
    wanted_hash = _result_hash(result_hash)

    try:
        check_chain(chain, settings)
        job = chain.read_job(escrow_job_id)
    except JobRefused as refused:
        return AcceptResult(
            AcceptOutcome.REFUSED, refused.detail, code=refused.code)
    except EscrowUnavailable as unavailable:
        return AcceptResult(
            AcceptOutcome.UNCERTAIN, str(unavailable),
            code=RejectCode.BLOCKCHAIN_RPC_FAILED)
    except Exception as error:
        return AcceptResult(
            AcceptOutcome.UNCERTAIN,
            f"the job could not be read ({type(error).__name__})",
            code=RejectCode.BLOCKCHAIN_RPC_FAILED)

    if job.state is JobState.COMPLETED:
        if _same_hex(job.result_hash, wanted_hash):
            return AcceptResult(
                AcceptOutcome.ACCEPTED,
                "the result was already recorded on chain", state=job.state)
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"escrow job {escrow_job_id} already holds a different result",
            code=RejectCode.RESULT_HASH_CONFLICT, state=job.state)
    if job.state is not JobState.ASSIGNED:
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"escrow job {escrow_job_id} is {job.state.name}, not ASSIGNED",
            code=RejectCode.INVALID_ESCROW_STATE, state=job.state)
    if not _same_address(job.operator, operator_address):
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"escrow job {escrow_job_id} was accepted by another operator and "
            "cannot be completed by this machine",
            code=RejectCode.OPERATOR_ADDRESS_MISMATCH, state=job.state)

    return _send_and_confirm(
        chain, escrow_job_id, operator_address,
        send=lambda job_id: chain.send_submit_result(job_id, wanted_hash),
        receipt_timeout=receipt_timeout,
        wanted=JobState.COMPLETED, what="result",
        result_hash=wanted_hash)


def _result_hash(value: str) -> str:
    """``0x`` and 64 lowercase hex digits, or a refusal.

    ``bytes32`` is thirty-two bytes and nothing else. A short hash would be
    left-padded by the encoder into a number that is not the file's digest,
    and it would be written to a contract that cannot be corrected.
    """
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        text = "0x" + text
    digits = text[2:]
    if len(digits) != 64 or any(c not in "0123456789abcdef" for c in digits):
        raise ValueError("resultHash is not 32 bytes of hex")
    return text


def _send_and_confirm(
    chain: ReadsEscrow,
    escrow_job_id: int,
    operator_address: str,
    *,
    send,
    receipt_timeout: float,
    wanted: JobState,
    what: str,
    result_hash: str | None = None,
) -> AcceptResult:
    """One transaction, and the chain's own answer about what it did.

    Shared by accept and abort because the hard part is identical and getting
    it right twice is how the two drift apart. The hard part is: a send that
    raised may still have reached the node, so the chain is asked rather than
    assumed -- in **either** direction. Assuming failure sends a second
    transaction for the same job; assuming success tells the Backend something
    that never happened.
    """
    try:
        transaction_hash = send(escrow_job_id)
    except Exception as error:
        _debug.info("escrow: %s send failed (%s) -- checking the job",
                    what, type(error).__name__)
        return _what_the_chain_says(
            chain, escrow_job_id, operator_address, wanted,
            f"the {what} transaction could not be confirmed "
            f"({type(error).__name__})", result_hash=result_hash)

    _debug.info("escrow: %s sent for job %s, tx %s",
                what, escrow_job_id, _short(transaction_hash))

    try:
        status = chain.receipt_status(transaction_hash, receipt_timeout)
    except Exception as error:
        return _what_the_chain_says(
            chain, escrow_job_id, operator_address, wanted,
            f"the receipt could not be read ({type(error).__name__})",
            transaction_hash, result_hash=result_hash)

    if status == 0:
        # Reverted. The chain has decided; nothing is pending.
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"the {what} transaction was rejected by the contract",
            transaction_hash, code=RejectCode.TRANSACTION_FAILED)
    if status is None:
        return _what_the_chain_says(
            chain, escrow_job_id, operator_address, wanted,
            f"the {what} transaction has not been confirmed yet",
            transaction_hash, result_hash=result_hash)

    return _what_the_chain_says(
        chain, escrow_job_id, operator_address, wanted,
        f"the {what} transaction was confirmed but the job did not change",
        transaction_hash, result_hash=result_hash)


def _what_the_chain_says(
    chain: ReadsEscrow,
    escrow_job_id: int,
    operator_address: str,
    wanted: JobState,
    otherwise: str,
    transaction_hash: str | None = None,
    result_hash: str | None = None,
) -> AcceptResult:
    """Read the job back and let it settle the question.

    Used both to confirm a receipt and to recover from not getting one. The
    same two facts decide either way: is the job in the state we were trying to
    reach, and is this machine its operator. If the read itself fails we still
    do not know, and saying so is the honest answer.
    """
    try:
        job = chain.read_job(escrow_job_id)
    except Exception as error:
        return AcceptResult(
            AcceptOutcome.UNCERTAIN,
            f"{otherwise}; the job could not be read back "
            f"({type(error).__name__})",
            transaction_hash, code=RejectCode.BLOCKCHAIN_RPC_FAILED)

    ours = _same_address(job.operator, operator_address)
    if (result_hash is not None and job.state is JobState.COMPLETED
            and not _same_hex(job.result_hash, result_hash)):
        # It completed, but holding somebody else's number. Never reported as
        # success: the chain and the storage would then be pointing at two
        # different files with one Job's name on them.
        return AcceptResult(
            AcceptOutcome.REFUSED,
            f"escrow job {escrow_job_id} holds a different result hash",
            transaction_hash, code=RejectCode.RESULT_HASH_CONFLICT,
            state=job.state)
    if job.state is wanted and (ours or wanted is JobState.CANCELLED):
        # A cancelled job clears its operator in some paths, so ownership is
        # not re-asserted for the abort case -- reaching CANCELLED at all is
        # the outcome that was wanted.
        return AcceptResult(
            AcceptOutcome.ACCEPTED,
            f"the job is {job.state.name.lower()}", transaction_hash,
            state=job.state)
    if job.state is JobState.FUNDED:
        # Nothing of ours landed; it is still waiting to be taken, so the
        # caller may stop holding the reservation.
        return AcceptResult(
            AcceptOutcome.REFUSED, otherwise, transaction_hash,
            code=RejectCode.TRANSACTION_FAILED, state=job.state)
    return AcceptResult(
        AcceptOutcome.UNCERTAIN, otherwise, transaction_hash,
        code=RejectCode.BLOCKCHAIN_RPC_FAILED, state=job.state)


def _same_address(left: str, right: str) -> bool:
    """Ethereum addresses compare case-insensitively; checksums vary by source."""
    return str(left).lower() == str(right).lower()


def _same_hex(left: str, right: str) -> bool:
    return str(left).lower() == str(right).lower()


def _as_hex(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    return str(value)


def _short(value: str) -> str:
    return f"{value[:8]}...{value[-6:]}" if len(value) > 18 else value


class Web3Escrow:
    """:class:`ReadsEscrow` over a real node. ``web3`` is imported inside it.

    Signing happens here and nowhere else, on one line, with the key held only
    for the length of that call. Nothing in this class puts the key in a
    message, a log line or an exception -- failures report the exception's type
    name and no more, because a library's message can carry what it was given.
    """

    def __init__(self, settings: EscrowSettings, wallet: Any,
                 artifact: EscrowArtifact | None = None) -> None:
        self._settings = settings
        self._wallet = wallet
        self._artifact = artifact or EscrowArtifact.load()
        check_contract_matches(self._artifact, JobState.as_mapping())
        self._web3 = None

    @property
    def artifact(self) -> EscrowArtifact:
        return self._artifact

    def _connection(self) -> Any:
        from web3 import HTTPProvider, Web3

        if self._web3 is None:
            self._web3 = Web3(HTTPProvider(self._settings.rpc_url))
        return self._web3

    def _contract(self) -> Any:
        web3 = self._connection()
        return web3.eth.contract(
            address=web3.to_checksum_address(self._settings.escrow_address),
            abi=self._artifact.abi)

    def chain_id(self) -> int:
        return int(self._connection().eth.chain_id)

    def has_contract_code(self) -> bool:
        web3 = self._connection()
        address = web3.to_checksum_address(self._settings.escrow_address)
        return len(web3.eth.get_code(address)) > 0

    def read_job(self, escrow_job_id: int) -> EscrowJob:
        return EscrowJob.from_row(
            self._contract().functions.jobs(int(escrow_job_id)).call())

    def find_funded_event(self, escrow_job_id: int) -> FundedEvent | None:
        """The one ``JobFunded`` log for this job id.

        ``jobId`` is indexed, so the node filters rather than this process. A
        job id is assigned once by the contract, so more than one log for it
        would mean something is badly wrong -- in that case None is returned
        and the caller refuses, rather than picking one.
        """
        contract = self._contract()
        logs = contract.events.JobFunded().get_logs(
            from_block=0, argument_filters={"jobId": int(escrow_job_id)})
        found = list(logs)
        if len(found) != 1:
            _debug.info("escrow: %s JobFunded logs for job %s",
                        len(found), escrow_job_id)
            return None
        args = found[0]["args"]
        return FundedEvent(
            escrow_job_id=int(args["jobId"]),
            external_ref=_as_hex(args["externalRef"]),
            provider=str(args["provider"]),
            client=str(args["client"]),
            price_wei=int(args["price"]),
            deadline=int(args["deadline"]),
        )

    def is_operator(self, provider: str, operator: str) -> bool:
        web3 = self._connection()
        return bool(self._contract().functions.isOperator(
            web3.to_checksum_address(provider),
            web3.to_checksum_address(operator)).call())

    def now(self) -> int:
        """The latest block's timestamp -- the chain's clock, not Windows'."""
        return int(self._connection().eth.get_block("latest")["timestamp"])

    def send_accept(self, escrow_job_id: int) -> str:
        return self._send(
            self._contract().functions.acceptJob(int(escrow_job_id)))

    def send_abort(self, escrow_job_id: int) -> str:
        return self._send(
            self._contract().functions.abortJob(int(escrow_job_id)))

    def send_submit_result(self, escrow_job_id: int, result_hash: str) -> str:
        """``submitResult``. The hash goes in as bytes, not as a string.

        web3 will accept a ``0x`` string for a ``bytes32`` and encode it, but
        converting here means the length is wrong *before* the transaction is
        built rather than inside the encoder.
        """
        raw = bytes.fromhex(_result_hash(result_hash)[2:])
        return self._send(
            self._contract().functions.submitResult(int(escrow_job_id), raw))

    def _send(self, call: Any) -> str:
        """Build, sign and broadcast one transaction.

        The nonce is read as ``pending`` so a second transaction built while a
        first is still in the mempool does not reuse its number and silently
        replace it.
        """
        web3 = self._connection()
        sender = web3.to_checksum_address(self._wallet.address)
        transaction = call.build_transaction({
            "from": sender,
            "nonce": web3.eth.get_transaction_count(sender, "pending"),
            "chainId": self._settings.chain_id,
        })
        # The only line in this package that uses the private key.
        signed = web3.eth.account.sign_transaction(
            transaction, self._wallet.private_key)
        return web3.to_hex(web3.eth.send_raw_transaction(signed.raw_transaction))

    def receipt_status(self, transaction_hash: str, timeout: float) -> int | None:
        from web3.exceptions import TimeExhausted

        try:
            receipt = self._connection().eth.wait_for_transaction_receipt(
                transaction_hash, timeout=timeout)
        except TimeExhausted:
            return None
        return int(receipt["status"])
