"""What the Backend says this Job actually is, and whether to believe it.

A JOB_ASSIGN is an announcement: it says a Job was given to this machine and
carries the escrow terms needed to accept it on chain. It does **not** carry
what to run. That comes from here, after the Job is accepted and the Backend
has moved it to RUNNING.

The whole of this module is one idea: **the context is compared against the
assignment, not trusted instead of it.** The Worker already signed a
transaction against the JOB_ASSIGN's terms. If the context that arrives a
second later describes a different price, a different client or a different
dataset, then one of the two is wrong and neither is safe to run -- so nothing
runs. There is no field here that the Worker fills in for itself when the
Backend leaves it out.

The one place the assignment does **not** win is the chain id and the contract
address: those are trusted configuration, and the context is checked against
them rather than the other way round. A Backend that could name the escrow
contract could name any contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .backend_client import BackendAnswer, BackendClient, Outcome
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: ``FUNDED``. The state the escrow job was in when it was accepted -- the
#: context reports the state acceptance *expected*, not the state now. A Job
#: that is RUNNING on the Backend is still ASSIGNED on chain, and this field
#: does not track that.
EXPECTED_ESCROW_STATE_FUNDED = 1

#: How long to keep asking after a 409.
#:
#: A 409 right after JOB_ACCEPTED is not a failure -- it is this Worker being
#: faster than the Backend's own state transition. The Job moves to RUNNING
#: when the WebSocket handler processes the message, and the HTTP request can
#: easily arrive first.
#:
#: Bounded, and small. Every one of these is a second in which nothing is
#: training, and a Backend that has not moved the Job after this long is not
#: about to.
CONTEXT_RETRY_ATTEMPTS = 6
CONTEXT_RETRY_FIRST_SECONDS = 0.5
CONTEXT_RETRY_MULTIPLIER = 2.0
CONTEXT_RETRY_MAX_SECONDS = 8.0


def context_path(job_id: str) -> str:
    from urllib.parse import quote

    return f"/api/worker/jobs/{quote(job_id, safe='')}/execution-context"


class ContextRefused(Exception):
    """The context could not be had, or could not be believed.

    ``fatal`` separates "this will never work" from "the chain or the service
    was briefly unavailable". Nothing here decides what to *do* about either --
    that is :mod:`gshared_worker.job_runtime`, which owns the Job.
    """

    def __init__(self, detail: str, *, fatal: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fatal = fatal


@dataclass(frozen=True)
class ModelContext:
    model_id: int
    model_ref: str
    model_revision: str


@dataclass(frozen=True)
class ImageContext:
    docker_image_id: int
    image_ref: str
    digest: str


@dataclass(frozen=True)
class DatasetContext:
    dataset_id: int
    download_url: str
    sha256: str
    size_bytes: int
    sample_count: int
    total_tokens: int
    effective_tokens: int
    avg_seq_length: float
    max_seq_length: int


@dataclass(frozen=True)
class TrainingContext:
    epochs: int
    batch_size: int
    learning_rate: float
    max_length: int


@dataclass(frozen=True)
class EscrowContext:
    chain_id: int
    contract_address: str | None
    escrow_job_id: int
    external_ref: str
    client: str
    provider: str
    expected_price_wei: int
    expected_state: int


@dataclass(frozen=True)
class ExecutionContext:
    """The whole of what a Job is, once it has been checked."""

    job_id: str
    assigned_worker_id: str
    model: ModelContext
    image: ImageContext
    dataset: DatasetContext
    training: TrainingContext
    escrow: EscrowContext


def fetch_execution_context(
    client: BackendClient,
    worker_token: str,
    job_id: str,
    *,
    sleep: Callable[[float], bool] | None = None,
    attempts: int = CONTEXT_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    """Ask the Backend what to run, retrying only what is worth retrying.

    ``sleep`` returns False when the Worker is stopping, which ends the wait
    immediately -- a person closing the window should not be held for the
    whole retry budget.

    Nothing is retried except a 409 (the Backend has not caught up yet) and a
    5xx or an unreachable service. A 401, 403 or 404 is a settled answer and
    asking again only wastes the accept deadline.
    """
    waiting = CONTEXT_RETRY_FIRST_SECONDS
    last = ""
    for attempt in range(1, attempts + 1):
        answered = client.request(
            "GET", context_path(job_id), bearer=worker_token)
        if answered.succeeded:
            body = answered.body
            if not isinstance(body, dict):
                raise ContextRefused(
                    "the execution context was not a JSON object")
            return body

        last = _why(answered)
        if not _worth_retrying(answered):
            raise ContextRefused(last)
        if attempt == attempts:
            break
        _debug.info("context: %s (attempt %s/%s), waiting %.1fs",
                    last, attempt, attempts, waiting)
        if sleep is not None and not sleep(waiting):
            raise ContextRefused(
                "the Worker is stopping, so the execution context was not "
                "fetched", fatal=False)
        waiting = min(waiting * CONTEXT_RETRY_MULTIPLIER,
                      CONTEXT_RETRY_MAX_SECONDS)

    raise ContextRefused(
        f"the execution context could not be fetched after {attempts} "
        f"attempts: {last}", fatal=False)


def _worth_retrying(answered: BackendAnswer) -> bool:
    if answered.outcome is Outcome.UNREACHABLE:
        return True
    status = answered.status
    if status is None:
        return False
    # 409: the Job has not become RUNNING yet. 5xx: the service, not the Job.
    return status == 409 or status >= 500


def _why(answered: BackendAnswer) -> str:
    if answered.outcome is Outcome.UNREACHABLE:
        return "the service could not be reached"
    status = answered.status
    if status == 401:
        return "the Worker token was refused"
    if status == 403:
        return "this Worker is not allowed to run that Job"
    if status == 404:
        return "the service has no such Job assigned to this Worker"
    if status == 409:
        return "the Job is not in a state that can be run yet"
    return f"the service answered {status}"


def read_context(body: dict[str, Any]) -> ExecutionContext:
    """Turn the response into typed values, or refuse it.

    Every field is required. A context missing its dataset hash or its model
    revision is not a context with a gap in it -- it is a Job that cannot be
    run safely, and filling the gap in here is the one thing that must never
    happen.
    """
    model = _object(body, "model")
    image = _object(body, "runtimeImage")
    dataset = _object(body, "dataset")
    training = _object(body, "trainingConfig")
    escrow = _object(body, "escrow")

    return ExecutionContext(
        job_id=_text(body, "jobId"),
        assigned_worker_id=_text(body, "assignedWorkerId"),
        model=ModelContext(
            model_id=_whole(model, "model.modelId"),
            model_ref=_text(model, "model.modelRef"),
            model_revision=_text(model, "model.modelRevision"),
        ),
        image=ImageContext(
            docker_image_id=_whole(image, "runtimeImage.dockerImageId"),
            image_ref=_text(image, "runtimeImage.imageRef"),
            digest=_text(image, "runtimeImage.digest"),
        ),
        dataset=DatasetContext(
            dataset_id=_whole(dataset, "dataset.datasetId"),
            download_url=_text(dataset, "dataset.downloadUrl"),
            sha256=_text(dataset, "dataset.sha256"),
            size_bytes=_whole(dataset, "dataset.datasetSizeBytes"),
            sample_count=_whole(dataset, "dataset.sampleCount"),
            total_tokens=_whole(dataset, "dataset.totalTokens"),
            effective_tokens=_whole(dataset, "dataset.effectiveTokens"),
            avg_seq_length=_number(dataset, "dataset.avgSeqLength"),
            max_seq_length=_whole(dataset, "dataset.maxSeqLength"),
        ),
        training=TrainingContext(
            epochs=_whole(training, "trainingConfig.epochs"),
            batch_size=_whole(training, "trainingConfig.batchSize"),
            learning_rate=_number(training, "trainingConfig.learningRate"),
            max_length=_whole(training, "trainingConfig.maxLength"),
        ),
        escrow=EscrowContext(
            chain_id=_whole(escrow, "escrow.chainId"),
            contract_address=_optional_text(escrow, "contractAddress"),
            escrow_job_id=_decimal(escrow, "escrow.escrowJobId"),
            external_ref=_text(escrow, "escrow.externalRef"),
            client=_text(escrow, "escrow.client"),
            provider=_text(escrow, "escrow.provider"),
            expected_price_wei=_decimal(escrow, "escrow.expectedPriceWei"),
            expected_state=_whole(escrow, "escrow.expectedState"),
        ),
    )


def check_matches_assignment(context: ExecutionContext, job: Any,
                             worker_id: str) -> None:
    """The context must describe the Job that was accepted. All of it.

    The escrow half is compared a second time here even though it was already
    checked against the chain before signing. That is deliberate: the values
    that were checked came from the JOB_ASSIGN, and this is a different message
    from the same service. If the two disagree, the Backend's own records
    disagree with each other, and the safe reading is that neither describes
    what is funded on chain.
    """
    _same("jobId", context.job_id, job.job_id)
    _same("assignedWorkerId", context.assigned_worker_id, worker_id)
    _same("dockerImageId", context.image.docker_image_id, job.docker_image_id)
    _same("modelId", context.model.model_id, job.model_id)
    _same("datasetId", context.dataset.dataset_id, job.dataset_id)

    _same("trainingConfig.epochs", context.training.epochs, job.training.epochs)
    _same("trainingConfig.batchSize",
          context.training.batch_size, job.training.batch_size)
    _same("trainingConfig.learningRate",
          context.training.learning_rate, job.training.learning_rate)
    _same("trainingConfig.maxLength",
          context.training.max_length, job.training.max_length)

    terms = job.escrow
    _same("escrow.escrowJobId",
          context.escrow.escrow_job_id, terms.escrow_job_id)
    _same_address("escrow.externalRef",
                  context.escrow.external_ref, terms.external_ref)
    _same_address("escrow.client", context.escrow.client, terms.client_wallet)
    _same_address("escrow.provider",
                  context.escrow.provider, terms.provider_wallet)
    # The Backend calls this expectedPriceWei. It is the payment token's
    # smallest unit, not ether wei -- the name is the Backend's and the meaning
    # is GCoin's.
    _same("escrow.expectedPriceWei",
          context.escrow.expected_price_wei, terms.price_wei)

    if context.escrow.expected_state != EXPECTED_ESCROW_STATE_FUNDED:
        # This field is what acceptance expected to find, not what the job is
        # now. A Backend that moved it to 2 or 3 because the Job is running has
        # changed the meaning of the field.
        raise ContextRefused(
            f"escrow.expectedState is {context.escrow.expected_state}, not "
            f"{EXPECTED_ESCROW_STATE_FUNDED} (FUNDED)")


def check_matches_trusted_escrow(context: ExecutionContext, settings: Any) -> None:
    """The chain and the contract come from configuration, not from a message.

    The context is allowed to agree, and nothing else. A ``contractAddress`` of
    null is accepted -- the Backend does not always know it -- but a different
    one is refused outright, because the Worker has already signed against the
    configured address and running against another would mean the money and the
    work are on two different contracts.
    """
    if context.escrow.chain_id != settings.chain_id:
        raise ContextRefused(
            f"the execution context is for chain {context.escrow.chain_id}, "
            f"and this Worker is configured for {settings.chain_id}")

    named = context.escrow.contract_address
    if named and named.lower() != str(settings.escrow_address).lower():
        raise ContextRefused(
            "the execution context names a different escrow contract than "
            "this Worker is configured to use")


# ---------------------------------------------------------------------------
# Reading one field at a time, refusing rather than defaulting
# ---------------------------------------------------------------------------

def _object(body: dict[str, Any], name: str) -> dict[str, Any]:
    value = body.get(name)
    if not isinstance(value, dict):
        raise ContextRefused(f"the execution context has no {name}")
    return value


def _text(body: dict[str, Any], path: str) -> str:
    value = body.get(path.rsplit(".", 1)[-1])
    if not isinstance(value, str) or not value.strip():
        raise ContextRefused(f"the execution context has no {path}")
    return value.strip()


def _optional_text(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextRefused(f"{name} is present but not a usable string")
    return value.strip()


def _whole(body: dict[str, Any], path: str) -> int:
    value = body.get(path.rsplit(".", 1)[-1])
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContextRefused(f"{path} is not a whole number")
    if value <= 0:
        raise ContextRefused(f"{path} is {value}, which is not a real value")
    return value


def _number(body: dict[str, Any], path: str) -> float:
    import math

    value = body.get(path.rsplit(".", 1)[-1])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContextRefused(f"{path} is not a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ContextRefused(f"{path} is {value}, which is not a real value")
    return number


def _decimal(body: dict[str, Any], path: str) -> int:
    """A uint256 sent as a decimal string, as the contract-facing values are."""
    value = body.get(path.rsplit(".", 1)[-1])
    if not isinstance(value, str):
        raise ContextRefused(
            f"{path} must be a decimal string, not a JSON number")
    text = value.strip()
    if not text.isdigit():
        raise ContextRefused(f"{path} is not an unsigned decimal integer")
    return int(text)


def _same(name: str, context_value: Any, assignment_value: Any) -> None:
    if context_value != assignment_value:
        raise ContextRefused(
            f"{name} does not match the assignment this Worker accepted "
            f"(context={context_value!r}, assignment={assignment_value!r})")


def _same_address(name: str, context_value: str, assignment_value: str) -> None:
    if str(context_value).lower() != str(assignment_value).lower():
        raise ContextRefused(
            f"{name} does not match the assignment this Worker accepted")
