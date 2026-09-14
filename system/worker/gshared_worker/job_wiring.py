"""The Job pipeline the connection stage hands every JOB_ASSIGN to.

Everything below already exists and is tested on its own: the escrow decision
and clean-up (6.5), the training runtime (7), progress (8), settlement (9) and
cancellation (10). This module is where they are put together for the real
Worker, once per run, and nothing else. It decides nothing about a Job.

**It refuses rather than guesses.** A Worker without an escrow contract to
sign against, without a usable signing key, or whose escrow client cannot be
built gets the plain :class:`~gshared_worker.job_acceptance.JobAcceptance`,
which refuses every assignment -- the behaviour every earlier build had. The
reason goes to the log. Accepting a Job this machine cannot settle would lock
a customer's deposit until the execution timeout.

The chain and the contract come from this Worker's own settings, never from a
message: a JOB_ASSIGN that could name the contract to sign for could name any.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from .auth_check import _http_origin_of
from .backend_client import BackendClient
from .config import WorkerSettings, data_directory
from .escrow import EscrowSettings, Web3Escrow
from .job_acceptance import (
    JobAcceptance,
    escrow_cleaner,
    escrow_decider,
    runtime_waiter,
)
from .job_cancellation import Cancellation
from .job_settlement import Settlement
from .logging_setup import LOGGER_NAME
from .runtime_pipeline import RuntimePipeline, RuntimeSettings
from .worker_credentials import WorkerCredentials
from .worker_wallet import load_wallet
from .wsl_runtime import WslCommandRunner

_debug = logging.getLogger(LOGGER_NAME)

#: Under the Worker's data directory. Job workspaces are ``work/jobs/<jobId>``
#: and model weights ``cache/models/<ref>__<revision>``; the Docker daemon in
#: the distribution reaches both through ``/mnt/<drive>``.
WORK_DIRECTORY_NAME = "work"
CACHE_DIRECTORY_NAME = "cache"


def escrow_settings_of(settings: WorkerSettings) -> EscrowSettings:
    """The escrow this Worker signs against, from its own settings only."""
    return EscrowSettings(
        rpc_url=settings.escrow_rpc_url or "",
        escrow_address=settings.escrow_address or "",
        chain_id=settings.chain_id,
    )


def build_job_acceptance(
    settings: WorkerSettings,
    credentials: WorkerCredentials,
    *,
    directory: Path | None = None,
    load: Callable[[], Any] | None = None,
    make_chain: Callable[[EscrowSettings, Any], Any] | None = None,
    runner: Any = None,
    client: Any = None,
) -> JobAcceptance:
    """Everything a JOB_ASSIGN needs, built for this run.

    The keyword arguments are the seams a test replaces; production passes
    none of them.
    """
    escrow = escrow_settings_of(settings)
    if not escrow.is_configured:
        _debug.warning(
            "jobs: no escrow contract is configured, so every assignment is refused")
        return JobAcceptance()

    loaded = (load or load_wallet)()
    if not loaded.is_present:
        _debug.warning(
            "jobs: this machine's signing key is not usable (%s), so every "
            "assignment is refused", loaded.state.value)
        return JobAcceptance()
    wallet = loaded.wallet

    try:
        chain = (make_chain or Web3Escrow)(escrow, wallet)
    except Exception as error:
        # The ABI could not be read or does not match this build. Only the
        # type: a library's message can carry what it was given.
        _debug.warning(
            "jobs: the escrow client could not be prepared (%s), so every "
            "assignment is refused", type(error).__name__)
        return JobAcceptance()

    base = Path(directory) if directory is not None else data_directory()
    runner = runner if runner is not None else WslCommandRunner()
    client = client if client is not None else BackendClient(
        _http_origin_of(settings.backend_url))
    runtime = RuntimeSettings(
        workspace_root=base / WORK_DIRECTORY_NAME,
        cache_root=base / CACHE_DIRECTORY_NAME,
        chain_id=escrow.chain_id,
        escrow_address=escrow.escrow_address,
    )
    operator = wallet.address

    pipeline = RuntimePipeline(
        client, credentials.worker_token, credentials.worker_id, runner, runtime)
    settlement = Settlement(
        client, credentials.worker_token, chain, escrow, operator,
        workspace_root=runtime.workspace_root)

    # The cancellation waits on the runtime thread the acceptance owns, so it
    # can only be built once the acceptance exists. The callable handed in is
    # the stable name; the object behind it is filled in straight after.
    built: list[Cancellation] = []

    def cancel_job(job: Any, terminal: Any, outcome: Any = None) -> Any:
        return built[0].cancel(job, terminal, outcome)

    acceptance = JobAcceptance(
        decide=escrow_decider(chain, escrow, operator),
        operator_address=operator,
        clean_up=escrow_cleaner(chain, escrow, operator),
        run_job=pipeline.run,
        settle_job=settlement.settle,
        cancel_job=cancel_job,
    )
    built.append(Cancellation(
        runner, chain, escrow, operator,
        wait_for_runtime=runtime_waiter(acceptance)))
    _debug.debug("jobs: pipeline ready for %s on chain %s",
                 credentials.worker_id, escrow.chain_id)
    return acceptance
