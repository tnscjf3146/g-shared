"""The Worker stage that decides whose machine this is.

Runs after RUNTIME READY, because there is no point attaching a machine to an
account before knowing it can do the work.

Two paths:

* **A machine that has been here before** -- credentials on disk, checked with
  ``POST /api/workers/authenticate``. No window is opened.
* **A machine that has not** -- a person signs in, ``POST /api/workers/register``
  attaches the machine to their account, and the identity it issues is sealed
  to this Windows account.

Registration has to state what this machine can do, so a new machine is
measured: the fixed fine-tuning workload in
:mod:`gshared_worker.benchmark_program` runs on the GPU, and the tokens per
second it managed go into the request. That takes about half a minute, which is
why it happens **after** somebody signs in rather than before. There is no point
spending a minute of a GPU for a machine nobody is attaching to an account, and
somebody who closed the window has not asked for anything.

A machine that has been here before is not measured again. It has an identity,
its number is already recorded against it, and re-measuring every start would
spend a minute of somebody GPU time to learn what is already known.
Re-measuring belongs to the GPU update endpoint, which is a flow of its own.

A benchmark that fails stops registration. There is no fallback figure: an
invented number is worse than no number, because the scheduler cannot tell it
from a measured one.
"""

from __future__ import annotations

import logging
from typing import Callable

from .backend_client import BackendClient
from .benchmark import BenchmarkReport, GpuBenchmark
from .config import WorkerSettings
from .exit_codes import WorkerExitCode
from .gui.presenter import AuthPresenter
from .gui.window import AuthWindow
from .logging_setup import LOGGER_NAME
from .machine_facts import MachineFacts, describe_machine
from .worker_auth import WorkerAuthResult, WorkerAuthService, WorkerAuthState, WorkerRegistry
from .worker_credentials import load_credentials, store_credentials
from .worker_registry import HttpWorkerRegistry, WorkerRegistrationRequest

_debug = logging.getLogger(LOGGER_NAME)

#: Measures what this machine can do. The real one runs a fine-tuning workload
#: in a container; a test supplies its own.
Benchmark = Callable[[], BenchmarkReport]

#: Reads this machine for the registration request.
DescribeMachine = Callable[[], "MachineFacts | None"]

#: What each state means to the person reading the console.
_IN_PLAIN_WORDS = {
    WorkerAuthState.CREDENTIAL_ERROR: (
        "The stored G-shared credentials cannot be read on this Windows account.",
        "Sign in on the account that registered this machine, or delete "
        "worker-config.json to register it afresh.",
    ),
    WorkerAuthState.REVOKED: (
        "This machine is no longer registered to a G-shared account.",
        "Register it again to start earning from it.",
    ),
    WorkerAuthState.AUTH_FAILED: (
        "The G-shared service refused this machine's credentials.",
        "Register it again to start earning from it.",
    ),
    WorkerAuthState.WORKER_NOT_FOUND: (
        "The G-shared service has no record of this machine.",
        "Nothing was changed here. If it was removed from your account, "
        "register it again to start earning from it.",
    ),
    WorkerAuthState.BACKEND_CONTRACT_ERROR: (
        "The G-shared service did not answer that request.",
        "Nothing was changed. This is a problem with the service rather than "
        "with this machine, so registering it again would not help.",
    ),
    WorkerAuthState.BACKEND_UNREACHABLE: (
        "The G-shared service could not be reached.",
        "Nothing was changed. Try again when it is back.",
    ),
}


def authenticate_worker(
    settings: WorkerSettings,
    say: Callable[..., None],
    *,
    registry: WorkerRegistry | None = None,
    ask_to_sign_in: Callable[[], str | None] | None = None,
    describe: DescribeMachine | None = None,
    benchmark: Benchmark | None = None,
) -> int:
    """Establish who this machine belongs to, or say what is in the way."""
    say("")
    say("Checking this machine's G-shared registration...")

    loaded = load_credentials()
    _debug.info("credentials: %s -- %s", loaded.state.value, loaded.detail)

    service = WorkerAuthService(registry or _backend_registry(settings))
    result = service.authenticate(loaded)

    if result.state is WorkerAuthState.NEEDS_ENROLLMENT:
        facts = _describe_this_machine(say, describe)
        if facts is None:
            # Said before a window is opened: asking somebody for a password
            # that could not be used is worse than not asking.
            return int(WorkerExitCode.EXPECTED_FAILURE)
        result = _enrol(service, settings, say, ask_to_sign_in, facts, benchmark)

    return _report(say, result)


def _backend_registry(settings: WorkerSettings) -> HttpWorkerRegistry:
    """The real Worker API, at the address this Worker is configured for."""
    return HttpWorkerRegistry(BackendClient(_http_origin_of(settings.backend_url)))


def _describe_this_machine(
    say: Callable[..., None], describe: DescribeMachine | None
) -> MachineFacts | None:
    """What this machine is, or None when it cannot say."""
    facts = (describe or describe_machine)()
    if facts is None:
        _debug.info("enrolment stopped: this machine could not be described")
        say("")
        say("G-shared cannot register this machine yet.")
        say("  Its GPU could not be described, so there is nothing to offer.")
        return None
    say(f"  This machine offers {facts.gpu_model} ({facts.vram_mb} MB).")
    return facts


def _measure_this_gpu(
    say: Callable[..., None], benchmark: Benchmark | None
) -> BenchmarkReport | None:
    """How fast this GPU fine-tunes, or None when it could not be measured.

    Never a fallback figure. A machine registered with a number nobody measured
    is a machine the scheduler sends work to on false pretences.
    """
    say("")
    say("Measuring what this GPU can do...")
    say("  It runs a short fine-tuning workload. This takes about a minute.")

    report = (benchmark or _run_the_benchmark)()
    if report.succeeded:
        say(f"  {report.result.summary()}")
        return report

    _debug.info("benchmark refused: %s -- %s", report.outcome.value, report.detail)
    say("")
    say("G-shared could not measure this GPU, so it was not registered.")
    say(f"  {report.detail}")
    say("  Nothing was changed here.")
    return None


def _run_the_benchmark() -> BenchmarkReport:
    """The real measurement, in our own distribution."""
    import asyncio

    return asyncio.run(GpuBenchmark().measure())


def _enrol(
    service: WorkerAuthService,
    settings: WorkerSettings,
    say: Callable[..., None],
    ask_to_sign_in: Callable[[], str | None] | None,
    facts: MachineFacts,
    benchmark: Benchmark | None,
) -> WorkerAuthResult:
    """Sign a person in, measure the GPU, and attach the machine to them."""
    say("  This machine is not registered yet. Sign in to attach it to your account.")

    ask = ask_to_sign_in or _open_sign_in_window(settings)
    access_token = ask()
    if not access_token:
        # The window was closed. Not a fault, nothing was changed, and no GPU
        # time was spent on a machine nobody is registering.
        return WorkerAuthResult(
            WorkerAuthState.NEEDS_ENROLLMENT,
            "Sign-in was cancelled, so this machine is still unregistered.",
        )

    measured = _measure_this_gpu(say, benchmark)
    if measured is None:
        del access_token
        return WorkerAuthResult(
            WorkerAuthState.NEEDS_ENROLLMENT,
            "This GPU could not be measured, so this machine is still unregistered.",
        )
    registration = WorkerRegistrationRequest.describing(
        facts, measured.result.tokens_per_sec)

    say("")
    say("  Registering this machine...")
    result, credentials = service.register(access_token, registration)
    # The access token has done its one job. It is not written anywhere and
    # nothing below this line can reach it.
    del access_token

    if credentials is not None:
        store_credentials(credentials.worker_id, credentials.worker_token)
    return result


def _open_sign_in_window(settings: WorkerSettings) -> Callable[[], str | None]:
    """The real window, built against the Backend this Worker is configured for."""
    def ask() -> str | None:
        client = BackendClient(_http_origin_of(settings.backend_url))
        return AuthWindow(AuthPresenter(client)).ask_to_sign_in()
    return ask


def _http_origin_of(backend_url: str) -> str:
    """The Backend's HTTP origin, from the WebSocket address the Worker has.

    One setting, so asking a second question of the same service means deriving
    the other scheme rather than making a supplier configure it twice and get
    them out of step.
    """
    from urllib.parse import urlparse

    parsed = urlparse(backend_url)
    scheme = "https" if parsed.scheme in ("wss", "https") else "http"
    return f"{scheme}://{parsed.netloc}"


def _what_is_left_to_say(detail: str, already_said: str) -> str:
    """The part of the detail that has not been said already.

    The plain words and the service's own message overlap -- both say the
    service could not be reached -- and printing the same sentence twice reads
    as two separate problems rather than one. What is left is often nothing,
    and sometimes the one clause that says something new.
    """
    before = _plainly(already_said)
    fresh = [
        sentence.strip() for sentence in detail.split(".")
        if sentence.strip() and _plainly(sentence) not in before
    ]
    return ". ".join(fresh)


def _plainly(text: str) -> str:
    """Enough of a sentence to tell it from a different one."""
    return " ".join(text.lower().replace(".", " ").split())


def _report(say: Callable[..., None], result: WorkerAuthResult) -> int:
    _debug.info("worker auth: %s -- %s", result.state.value, result.detail)

    if result.is_authenticated:
        say(f"  Registered as {result.worker_id}.")
        say("")
        say("AUTH READY.")
        say("")
        say("Connecting to the Backend is not part of this build yet.")
        return int(WorkerExitCode.OK)

    said, remedy = _IN_PLAIN_WORDS.get(
        result.state, (result.detail, None))
    say("")
    say(said)
    if remedy:
        say(f"  {remedy}")
    left = _what_is_left_to_say(result.detail, f"{said} {remedy or ''}")
    if left:
        say(f"  {left}")

    # A service that is down is worth retrying; the rest are not. Telling them
    # apart is the whole reason these are different states.
    if result.state is WorkerAuthState.BACKEND_UNREACHABLE:
        return int(WorkerExitCode.TRANSPORT_FAILURE)
    if result.state in (WorkerAuthState.REVOKED, WorkerAuthState.AUTH_FAILED,
                        WorkerAuthState.CREDENTIAL_ERROR,
                        WorkerAuthState.WORKER_NOT_FOUND):
        return int(WorkerExitCode.CREDENTIAL_MISMATCH)
    # BACKEND_CONTRACT_ERROR lands here: not the credential's fault, and not
    # worth retrying either -- a path that is not there stays not there.
    return int(WorkerExitCode.EXPECTED_FAILURE)
