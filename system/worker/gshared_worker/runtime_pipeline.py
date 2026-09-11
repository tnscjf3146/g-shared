"""From an accepted Job to a finished container, in order.

Each stage here is cheap before it is expensive, and every one of them can
stop the Job. That ordering is the design: the dataset is not downloaded until
the context has been checked against what was accepted, the image is not
pulled until it is known to be an approved one, and the GPU is not touched
until everything it needs is on disk and verified.

::

    execution-context   ->  compared against the accepted assignment
    runtime image       ->  approved repository, immutable digest
    model               ->  pinned commit, fetched once, cached
    dataset             ->  streamed, hashed, size-checked
    docker pull         ->  by digest, then confirmed locally
    docker run          ->  hardened, GPU, no network
    outcome             ->  returned, not sent

**Nothing here talks to the Backend except to read the context.** No
JOB_PROGRESS, no JOB_RESULT, no JOB_FAILED. The pipeline returns a
:class:`~gshared_worker.job_runtime.RuntimeOutcome` and stops. Task 8 will
turn its events into progress and task 9 will turn the outcome into a terminal
message; both are easier to write against a value than against a function that
has already sent something.

**It runs on a background thread**, because a training run is minutes to hours
and the WebSocket loop has a heartbeat to keep. That is the same arrangement
escrow acceptance uses, and for the same reason.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .dataset_fetch import DatasetRefused, fetch_dataset
from .execution_context import (
    ContextRefused,
    check_matches_assignment,
    check_matches_trusted_escrow,
    fetch_execution_context,
    read_context,
)
from .job_runtime import (
    RuntimeFailure,
    RuntimeOutcome,
    RuntimeState,
    run_training,
)
from .job_workspace import JobWorkspace, ModelCache, UnsafePath, to_distro_path
from .logging_setup import LOGGER_NAME
from .model_prep import ModelRefused, prepare_model
from .runtime_image import (
    ImageRefused,
    check_image,
    check_pulled,
    inspect_command,
    pull_command,
)

_debug = logging.getLogger(LOGGER_NAME)

PULL_TIMEOUT_SECONDS = 3600
INSPECT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class RuntimeSettings:
    """Where this machine keeps things, and what it will run.

    Separate from :class:`~gshared_worker.config.WorkerSettings` because none
    of it is answerable by a supplier: the approved repository is a security
    boundary, not a preference.
    """

    workspace_root: Path
    cache_root: Path
    chain_id: int
    escrow_address: str


class RuntimePipeline:
    """Prepares and runs one Job. Holds nothing between Jobs except the cache."""

    def __init__(
        self,
        client: Any,
        worker_token: str,
        worker_id: str,
        runner: Any,
        settings: RuntimeSettings,
        *,
        sleep: Callable[[float], bool] | None = None,
        host_path_of: Callable[[Path], str] = to_distro_path,
    ) -> None:
        self._client = client
        self._token = worker_token
        self._worker_id = worker_id
        self._runner = runner
        self._settings = settings
        self._sleep = sleep
        self._host_path_of = host_path_of
        self._cache = ModelCache(settings.cache_root)

    def run(self, job: Any) -> RuntimeOutcome:
        """The whole pipeline for one Job, synchronously. Call off the loop."""
        try:
            return asyncio.run(self._run(job))
        except Exception as error:
            # The background thread must always produce an outcome. An
            # exception that escaped would otherwise leave the Job with no
            # answer at all and the slot held for ever.
            _debug.exception("runtime: pipeline failed for %s", job.job_id)
            return RuntimeOutcome(
                RuntimeState.FAILED, RuntimeFailure.DOCKER_UNAVAILABLE,
                f"the runtime pipeline failed ({type(error).__name__})")

    async def _run(self, job: Any) -> RuntimeOutcome:
        # -- what to run ------------------------------------------------
        try:
            body = fetch_execution_context(
                self._client, self._token, job.job_id, sleep=self._sleep)
            context = read_context(body)
            check_matches_assignment(context, job, self._worker_id)
            check_matches_trusted_escrow(context, self._settings)
        except ContextRefused as refused:
            return _failed(
                RuntimeFailure.CONTEXT_MISMATCH if refused.fatal
                else RuntimeFailure.CONTEXT_UNAVAILABLE, refused.detail)

        # -- which container --------------------------------------------
        try:
            image = check_image(context.image.image_ref, context.image.digest)
        except ImageRefused as refused:
            return _failed(RuntimeFailure.IMAGE_REFUSED, str(refused))

        # -- the weights ------------------------------------------------
        try:
            model = await prepare_model(
                self._runner, self._cache, image.reference,
                context.model.model_ref, context.model.model_revision,
                host_path_of=self._host_path_of)
        except (ModelRefused, UnsafePath) as refused:
            return _failed(
                RuntimeFailure.MODEL_PREPARATION_FAILED, str(refused))

        # -- the data ---------------------------------------------------
        try:
            workspace = JobWorkspace.under(
                self._settings.workspace_root, job.job_id).prepare()
            fetch_dataset(
                context.dataset.download_url, workspace.dataset_path,
                expected_sha256=context.dataset.sha256,
                expected_size=context.dataset.size_bytes,
                sleep=self._sleep)
        except (DatasetRefused, UnsafePath) as refused:
            return _failed(RuntimeFailure.DATASET_UNAVAILABLE, str(refused))
        except OSError as error:
            return _failed(
                RuntimeFailure.DATASET_UNAVAILABLE,
                f"the job workspace could not be prepared "
                f"({type(error).__name__})")

        # -- the image, actually here -----------------------------------
        pulled = await self._pull(image)
        if pulled is not None:
            return pulled

        # -- and finally the GPU ----------------------------------------
        _debug.info("runtime: %s ready, starting training", job.job_id)
        return await run_training(
            self._runner, image.reference, workspace, model.path,
            context.training, host_path_of=self._host_path_of)

    async def _pull(self, image: Any) -> RuntimeOutcome | None:
        """Fetch by digest and confirm what landed. None means it is ready.

        The confirmation runs even when the image was already cached. A cache
        hit proves some earlier run pulled *something* under this reference;
        it is not evidence about what is in the store now.
        """
        try:
            fetched = await self._runner.run(
                pull_command(image), timeout=PULL_TIMEOUT_SECONDS,
                max_output_bytes=64 * 1024)
        except Exception as error:
            return _failed(
                RuntimeFailure.DOCKER_UNAVAILABLE,
                f"the Docker engine did not answer ({type(error).__name__})")
        if fetched is None or fetched.exit_code != 0:
            return _failed(
                RuntimeFailure.IMAGE_PULL_FAILED,
                "the approved runtime image could not be fetched")

        try:
            found = await self._runner.run(
                inspect_command(image), timeout=INSPECT_TIMEOUT_SECONDS,
                max_output_bytes=64 * 1024)
        except Exception as error:
            return _failed(
                RuntimeFailure.DOCKER_UNAVAILABLE,
                f"the Docker engine did not answer ({type(error).__name__})")
        if found is None or found.exit_code != 0:
            return _failed(
                RuntimeFailure.IMAGE_PULL_FAILED,
                "the fetched image could not be inspected")
        try:
            check_pulled(image, found.stdout)
        except ImageRefused as refused:
            return _failed(RuntimeFailure.IMAGE_REFUSED, str(refused))
        return None


def _failed(failure: RuntimeFailure, detail: str) -> RuntimeOutcome:
    _debug.info("runtime: %s -- %s", failure.value, detail)
    return RuntimeOutcome(RuntimeState.FAILED, failure, detail)
