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
from typing import Any, Callable

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
from . import __version__
from .benchmark_program import BENCHMARK_VERSION
from .worker_registry import HttpWorkerRegistry, WorkerRegistrationRequest
from .worker_wallet import WalletError, prepare_wallet

_debug = logging.getLogger(LOGGER_NAME)

#: Measures what this machine can do. The real one runs a fine-tuning workload
#: in a container; a test supplies its own.
Benchmark = Callable[[], BenchmarkReport]

#: Reads this machine for the registration request.
DescribeMachine = Callable[[], "MachineFacts | None"]

#: What each state means to the person reading the console.
_IN_PLAIN_WORDS = {
    WorkerAuthState.CREDENTIAL_ERROR: (
        "저장된 G-shared 인증 정보를 이 Windows 계정에서 읽을 수 없습니다.",
        "이 PC를 등록한 Windows 계정으로 실행하거나, worker-config.json을 지운 뒤 다시 등록하세요.",
    ),
    WorkerAuthState.REVOKED: (
        "이 PC는 더 이상 G-shared 계정에 등록되어 있지 않습니다.",
        "수익을 받으려면 다시 등록하세요.",
    ),
    WorkerAuthState.AUTH_FAILED: (
        "G-shared 서비스가 이 PC의 인증 정보를 거부했습니다.",
        "수익을 받으려면 다시 등록하세요.",
    ),
    WorkerAuthState.WORKER_NOT_FOUND: (
        "G-shared 서비스에 이 PC의 등록 기록이 없습니다.",
        "이 PC에서는 아무것도 바뀌지 않았습니다. 계정에서 삭제했다면 다시 등록하세요.",
    ),
    WorkerAuthState.BACKEND_CONTRACT_ERROR: (
        "G-shared 서비스가 그 요청에 응답하지 않았습니다.",
        "아무것도 바뀌지 않았습니다. 이 PC가 아니라 서비스 쪽 문제라서 다시 등록해도 해결되지 않습니다.",
    ),
    WorkerAuthState.BACKEND_UNREACHABLE: (
        "G-shared 서비스에 연결할 수 없습니다.",
        "아무것도 바뀌지 않았습니다. 서비스가 복구되면 다시 시도하세요.",
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
    make_wallet: Callable[..., Any] | None = None,
) -> int:
    """Establish who this machine belongs to, or say what is in the way."""
    say("")
    say("이 PC의 G-shared 등록 상태를 확인하는 중...")

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
        result = _enrol(service, settings, say, ask_to_sign_in, facts,
                        benchmark, make_wallet)

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
        say("아직 이 PC를 등록할 수 없습니다.")
        say("  GPU 정보를 읽지 못해 등록할 내용이 없습니다.")
        return None
    say(f"  이 PC의 GPU: {facts.gpu_model} ({facts.vram_mb} MB)")
    return facts


def _measure_this_gpu(
    say: Callable[..., None], benchmark: Benchmark | None
) -> BenchmarkReport | None:
    """How fast this GPU fine-tunes, or None when it could not be measured.

    Never a fallback figure. A machine registered with a number nobody measured
    is a machine the scheduler sends work to on false pretences.
    """
    say("")
    say("GPU 성능을 측정하는 중...")
    say("  짧은 파인튜닝 작업을 실행합니다. 약 1분 걸립니다.")

    report = (benchmark or _run_the_benchmark)()
    if report.succeeded:
        say(f"  {report.result.summary()}")
        return report

    _debug.info("benchmark refused: %s -- %s", report.outcome.value, report.detail)
    say("")
    say("GPU 성능을 측정하지 못해 등록하지 않았습니다.")
    say(f"  {report.detail}")
    say("  이 PC에서는 아무것도 바뀌지 않았습니다.")
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
    make_wallet: Callable[..., Any] | None = None,
) -> WorkerAuthResult:
    """Sign a person in, measure the GPU, and attach the machine to them."""
    say("  이 PC는 아직 등록되지 않았습니다. 로그인하면 계정에 연결됩니다.")

    ask = ask_to_sign_in or _open_sign_in_window(settings)
    access_token = ask()
    if not access_token:
        # The window was closed. Not a fault, nothing was changed, and no GPU
        # time was spent on a machine nobody is registering.
        return WorkerAuthResult(
            WorkerAuthState.NEEDS_ENROLLMENT,
            "로그인을 취소해 이 PC는 아직 등록되지 않았습니다.",
        )

    measured = _measure_this_gpu(say, benchmark)
    if measured is None:
        del access_token
        return WorkerAuthResult(
            WorkerAuthState.NEEDS_ENROLLMENT,
            "GPU 성능을 측정하지 못해 이 PC는 아직 등록되지 않았습니다.",
        )
    # The signing key comes before registration, because the address it
    # derives is a required registration field now. A Worker registered
    # without one cannot be approved on chain, so it would be handed work it
    # could never accept.
    try:
        wallet = (make_wallet or prepare_wallet)(say)
    except WalletError as refused:
        # A key file that is there and cannot be read is never replaced. It
        # holds an on-chain identity, and writing over it would lose whatever
        # that identity still owes or is owed.
        del access_token
        return WorkerAuthResult(
            WorkerAuthState.CREDENTIAL_ERROR,
            f"이 PC의 서명 키를 준비하지 못했습니다: {refused}")
    registration = WorkerRegistrationRequest.describing(
        facts, measured.result.tokens_per_sec,
        operator_address=wallet.address,
        client_version=__version__,
        benchmark_version=BENCHMARK_VERSION)

    say("")
    say("  이 PC를 등록하는 중...")
    result, credentials = service.register(access_token, registration)
    # The access token has done its one job. It is not written anywhere and
    # nothing below this line can reach it.
    del access_token

    if credentials is None:
        return result
    store_credentials(credentials.worker_id, credentials.worker_token)
    # Read back what was just sealed and ask the Backend about it, the same way
    # every later start does. A token that cannot be unsealed on this account,
    # or that the Backend does not accept, is found here -- before connecting
    # -- rather than on the next start after the window has gone.
    return service.authenticate(load_credentials())


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
        say(f"  등록되었습니다: {result.worker_id}")
        say("")
        say("등록 확인 완료.")
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
