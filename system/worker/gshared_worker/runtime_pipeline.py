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

**Nothing here talks to the Backend except to read the context.** It sends no
JOB_PROGRESS, no JOB_RESULT and no JOB_FAILED, and it holds no socket. The
pipeline returns a :class:`~gshared_worker.job_runtime.RuntimeOutcome` and
stops.

Progress leaves by a different door. A caller may pass a
:class:`~gshared_worker.runtime_progress.ProgressTracker`, which is given the
container's events while it runs and puts the newest one in a box the
connection loop reads. The pipeline still sends nothing: publishing a value
and writing to a socket are two different acts, and only one of them can
happen on this thread. Task 9 will turn the returned outcome into a terminal
message the same way.

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

from .adapter_fetch import AdapterRefused, fetch_adapter
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
from . import status_events
from .status_events import JobPhase, JobPhaseChanged
from .runtime_image import (
    ImageRefused,
    check_image,
    check_pulled,
    inspect_command,
    pull_command,
    supports_adapter,
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

    def run(self, job: Any, *, progress: Any = None,
            terminal: Any = None) -> RuntimeOutcome:
        """The whole pipeline for one Job, synchronously. Call off the loop.

        ``progress`` is a :class:`~gshared_worker.runtime_progress.\
ProgressTracker` when somebody is watching. It is handed the container's
        events as they are written and publishes what is worth publishing;
        this function neither reads it back nor sends anything itself.
        """
        try:
            return asyncio.run(self._run(job, progress, terminal))
        except Exception as error:
            # The background thread must always produce an outcome. An
            # exception that escaped would otherwise leave the Job with no
            # answer at all and the slot held for ever.
            _debug.exception("runtime: pipeline failed for %s", job.job_id)
            return RuntimeOutcome(
                RuntimeState.FAILED, RuntimeFailure.DOCKER_UNAVAILABLE,
                f"the runtime pipeline failed ({type(error).__name__})")

    async def _run(self, job: Any, progress: Any = None,
                   terminal: Any = None) -> RuntimeOutcome:
        """Each phase asks, before it starts, whether the Job is still wanted.

        The checks are between phases rather than inside them. A cancellation
        arriving during a dataset download does not tear the download apart;
        it means the image is not pulled afterwards and the container is not
        started at all. The phases are minutes long, so the worst wait is one
        phase, and the one that matters -- ``docker run`` -- is checked in the
        moment before it happens.
        """
        if _stop_now(terminal):
            return _cancelled_before_starting("before the execution context")
        # -- what to run ------------------------------------------------
        _announce(job, JobPhase.CONTEXT)
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

        if _stop_now(terminal):
            return _cancelled_before_starting("before the runtime image")

        # -- which container --------------------------------------------
        try:
            image = check_image(context.image.image_ref, context.image.digest)
        except ImageRefused as refused:
            return _failed(RuntimeFailure.IMAGE_REFUSED, str(refused))

        if context.adapter is not None and not supports_adapter(image):
            # An approved runtime that predates --adapter-path. Said now,
            # before the weights and the dataset are fetched for a run that
            # could only ever train from scratch -- which is not what the
            # customer asked for.
            return _failed(
                RuntimeFailure.ADAPTER_UNAVAILABLE,
                "the runtime image this Job names cannot continue training "
                "from an adapter")

        if _stop_now(terminal):
            return _cancelled_before_starting("before preparing the model")

        # -- the weights ------------------------------------------------
        _announce(job, JobPhase.MODEL, cached=self._model_cached(context))
        try:
            model = await prepare_model(
                self._runner, self._cache, image.reference,
                context.model.model_ref, context.model.model_revision,
                host_path_of=self._host_path_of)
        except (ModelRefused, UnsafePath) as refused:
            return _failed(
                RuntimeFailure.MODEL_PREPARATION_FAILED, str(refused))

        if _stop_now(terminal):
            return _cancelled_before_starting("before fetching the dataset")

        # -- the data ---------------------------------------------------
        _announce(job, JobPhase.DATASET)
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

        # -- the adapter to continue from, if the Job has one ----------------
        #
        # After the dataset, before the image: both are the customer's inputs
        # and both are checked before anything heavier happens. A Job that
        # asked for an adapter is never trained without it.
        if context.adapter is not None:
            if _stop_now(terminal):
                return _cancelled_before_starting("before fetching the adapter")
            _announce(job, JobPhase.ADAPTER)
            try:
                fetch_adapter(
                    context.adapter.download_url,
                    workspace.adapter_archive_path, workspace.adapter_dir,
                    expected_sha256=context.adapter.sha256,
                    expected_size=context.adapter.size_bytes,
                    sleep=self._sleep)
            except AdapterRefused as refused:
                return _failed(RuntimeFailure.ADAPTER_UNAVAILABLE, refused.detail)
            except OSError as error:
                return _failed(
                    RuntimeFailure.ADAPTER_UNAVAILABLE,
                    f"the adapter could not be written to the job workspace "
                    f"({type(error).__name__})")

        if _stop_now(terminal):
            return _cancelled_before_starting("before pulling the image")

        # -- the image, actually here -----------------------------------
        _announce(job, JobPhase.IMAGE)
        pulled = await self._pull(image)
        if pulled is not None:
            return pulled

        # -- and finally the GPU ----------------------------------------
        #
        # The last check, and the one the others exist for. Everything above
        # was preparation nobody sees; below this line a GPU starts working
        # on a Job the customer has asked to stop.
        if _stop_now(terminal):
            return _cancelled_before_starting("before starting the container")

        _debug.debug("runtime: %s ready, starting training", job.job_id)
        _announce(job, JobPhase.TRAINING)
        generation = getattr(terminal, "generation", 0)
        return await run_training(
            self._runner, image.reference, workspace, model.path,
            context.training, host_path_of=self._host_path_of,
            on_event=progress.observe if progress is not None else None,
            generation=generation,
            cancelled=(lambda: terminal.cancel_requested)
            if terminal is not None else None,
            on_started=_remember(terminal, job.job_id, generation),
            adapter=context.adapter is not None)

    def _model_cached(self, context: Any) -> bool | None:
        """Whether the weights are already here. None when it cannot be told."""
        try:
            return self._cache.is_complete(
                context.model.model_ref, context.model.model_revision)
        except (UnsafePath, OSError, ValueError):
            # prepare_model reports a bad reference properly a moment later.
            return None

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
    _debug.warning("runtime: %s -- %s", failure.value, detail)
    return RuntimeOutcome(RuntimeState.FAILED, failure, detail)


def _announce(job: Any, phase: JobPhase, *, cached: bool | None = None) -> None:
    """Tell a watcher which phase this Job has reached. A value, never a widget."""
    status_events.publish(JobPhaseChanged(job.job_id, phase, cached))


def _stop_now(terminal: Any) -> bool:
    """Whether this Job has been cancelled since the last phase."""
    return terminal is not None and terminal.cancel_requested


def _cancelled_before_starting(where: str) -> RuntimeOutcome:
    """Stopped during preparation. **Not a failure, and no runtime.**

    ``actual_runtime_sec`` stays None: no container ran, so there is no
    duration, and a zero would be a measurement nobody made.
    """
    _debug.debug("runtime: stopped %s", where)
    return RuntimeOutcome(
        RuntimeState.CANCELLED, None,
        f"the job was stopped at the customer's request {where}")


def _remember(terminal: Any, job_id: str, generation: int):
    """Hand the container's identity to whoever may have to stop it.

    Called the moment ``docker run`` goes out, so a cancellation arriving a
    millisecond later has something specific to stop rather than a name it
    would have to guess at.
    """
    if terminal is None:
        return None

    def started(container_name: str) -> None:
        from .runtime_stop import RuntimeHandle

        terminal.container_started(RuntimeHandle(
            job_id=job_id, generation=generation,
            container_name=container_name))

    return started
