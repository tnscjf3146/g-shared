"""What the status window shows, and what closing it means. Decided without Tk.

The same split as :mod:`gshared_worker.gui.presenter` and the sign-in window:
every decision is here, where a test can make it, and
:mod:`gshared_worker.gui.status_window` only draws what it is given.

**Only the window's thread calls this.** Events reach it through a queue the
window drains; nothing on a Worker thread ever holds a reference to it.

What the window shows is its own state, built from two things the Worker
publishes and kept apart from both: where the run is
(:class:`~gshared_worker.status_events.WorkerStage`) and what the Job slot
holds (:class:`~gshared_worker.status_events.JobStage`). ``STOPPING``,
``ERROR`` and ``STOPPED`` exist only here -- they are what a person sees, not
states the Worker is in.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..status_events import (
    GpuDescribed,
    JobChanged,
    JobEnd,
    JobEnded,
    JobPhase,
    JobPhaseChanged,
    JobStage,
    Said,
    StageChanged,
    WorkerOnline,
    WorkerStage,
    redact,
    shorten,
)


class Display(Enum):
    """What the headline says. The window's state, nobody else's."""

    STARTING = "STARTING"
    ENVIRONMENT_CHECK = "ENVIRONMENT_CHECK"
    RUNTIME_PREPARING = "RUNTIME_PREPARING"
    AUTHENTICATING = "AUTHENTICATING"
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    RECONNECTING = "RECONNECTING"
    JOB_ASSIGNED = "JOB_ASSIGNED"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class Tone(Enum):
    """The colour of the dot beside the headline."""

    BUSY = "BUSY"
    GOOD = "GOOD"
    WORK = "WORK"
    WARN = "WARN"
    BAD = "BAD"
    DONE = "DONE"


_HEADLINE = {
    Display.STARTING: ("시작하는 중", Tone.BUSY),
    Display.ENVIRONMENT_CHECK: ("시스템 환경 확인 중", Tone.BUSY),
    Display.RUNTIME_PREPARING: ("GPU 실행 환경 준비 중", Tone.BUSY),
    Display.AUTHENTICATING: ("Worker 인증 중", Tone.BUSY),
    Display.REGISTRATION_REQUIRED: ("Worker 등록이 필요합니다", Tone.WARN),
    Display.CONNECTING: ("서버 연결 중", Tone.BUSY),
    Display.ONLINE: ("온라인", Tone.GOOD),
    Display.RECONNECTING: ("다시 연결하는 중", Tone.WARN),
    Display.JOB_ASSIGNED: ("작업 준비 중", Tone.WORK),
    Display.RUNNING: ("작업 실행 중", Tone.WORK),
    Display.STOPPING: ("종료 중", Tone.WARN),
    Display.ERROR: ("오류", Tone.BAD),
    Display.STOPPED: ("종료됨", Tone.DONE),
}

_ENDED_LINE = {
    JobEnd.COMPLETED: "작업이 완료되었습니다.",
    JobEnd.FAILED: "작업 실행에 실패했습니다.",
    JobEnd.CANCELLED: "작업이 취소되었습니다.",
}

#: What the job row says in each phase.
_PHASE_LABEL = {
    JobPhase.CONTEXT: "실행 정보를 확인하는 중",
    JobPhase.MODEL: "모델을 준비하는 중",
    JobPhase.DATASET: "데이터셋을 내려받는 중",
    JobPhase.ADAPTER: "어댑터를 내려받는 중",
    JobPhase.IMAGE: "실행 이미지를 확인하는 중",
    JobPhase.TRAINING: "학습 진행 중",
    JobPhase.SETTLING: "결과를 올리고 정산하는 중",
}

#: Phases before the GPU is training. The headline says "작업 준비 중" for these.
_PREPARING = frozenset({JobPhase.CONTEXT, JobPhase.MODEL, JobPhase.DATASET,
                        JobPhase.ADAPTER, JobPhase.IMAGE})

_ENDED_SHORT = {
    JobEnd.COMPLETED: "완료",
    JobEnd.FAILED: "실패",
    JobEnd.CANCELLED: "취소됨",
}

AFTER_WORK_TITLE = "작업 진행 중"
AFTER_WORK_QUESTION = (
    "이 PC가 작업을 처리하고 있습니다.\n\n"
    "작업을 마치고 결과를 보낸 뒤 종료할까요?\n"
    "'아니요'를 누르면 계속 실행합니다.")

NOW_TITLE = "지금 종료"
NOW_QUESTION = (
    "작업이 끝나기를 기다리는 중입니다.\n\n"
    "지금 종료하면 실행 중인 학습이 정리되지 않은 채 남고\n"
    "작업이 실패로 처리될 수 있습니다.\n\n"
    "그래도 지금 종료할까요?")


class ShutdownAction(Enum):
    """What closing the window turned out to mean."""

    #: Nothing: the person said no, or a stop is already under way.
    IGNORE = "IGNORE"
    #: Stop at the next checkpoint.
    STOP_NOW = "STOP_NOW"
    #: Stop once the Job in hand has been reported.
    STOP_AFTER_WORK = "STOP_AFTER_WORK"
    #: The Worker has already finished. Close the window.
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class WorkerFinished:
    """The Worker thread returned. Published by the app, not by the Worker."""

    exit_code: int


@dataclass(frozen=True)
class FailureToShow:
    """The text the failure dialog shows -- the same text it always showed."""

    text: str


@dataclass
class SignInRequest:
    """The Worker thread asking the window's thread to run the sign-in window.

    The Worker thread waits on ``done``; the window's thread runs the window,
    puts the answer in ``result`` and sets it. ``result`` is an access token and
    is kept out of the repr, so it cannot reach a log by accident.
    """

    window: Any
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    result: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class StatusView:
    headline: str
    tone: Tone
    gpu: str
    worker: str
    job: str
    progress: str
    button_text: str
    button_enabled: bool


class StatusPresenter:
    """The window's state. Applied one event at a time, on the window's thread."""

    #: How many lines the log keeps. Enough to scroll back through a start-up
    #: and a Job; not so many that a week online becomes a memory leak.
    MAX_LINES = 200

    def __init__(self, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or (lambda: time.strftime("%H:%M:%S"))
        self.stage = WorkerStage.STARTING
        self.job = JobChanged(None, JobStage.IDLE)
        #: The latest phase of the Job in hand. None before the pipeline says.
        self.job_phase: JobPhaseChanged | None = None
        self.last_end: JobEnded | None = None
        self.gpu: GpuDescribed | None = None
        self.worker_id: str | None = None
        #: None, "after_work" or "now".
        self.stop_mode: str | None = None
        self.exit_code: int | None = None
        self.failure: str | None = None
        self._lines: deque[str] = deque(maxlen=self.MAX_LINES)
        self._written = 0

    # -- events -----------------------------------------------------------

    def apply(self, event: Any) -> None:
        if isinstance(event, Said):
            self._line(event.text)
        elif isinstance(event, StageChanged):
            self.stage = event.stage
        elif isinstance(event, GpuDescribed):
            self.gpu = event
        elif isinstance(event, WorkerOnline):
            self.worker_id = event.worker_id
        elif isinstance(event, JobChanged):
            self.job = event
            if self.job_phase is not None and event.job_id != self.job_phase.job_id:
                # The slot emptied or holds another Job: the phase was the old one's.
                self.job_phase = None
        elif isinstance(event, JobPhaseChanged):
            self._enter_phase(event)
        elif isinstance(event, JobEnded):
            self.last_end = event
            self.job = JobChanged(None, JobStage.IDLE)
            self.job_phase = None
            self._line(_ENDED_LINE[event.end])
        elif isinstance(event, FailureToShow):
            self.failure = redact(event.text)
        elif isinstance(event, WorkerFinished):
            self.exit_code = int(event.exit_code)

    def _enter_phase(self, event: JobPhaseChanged) -> None:
        """Record the phase, and say it once when it is new."""
        previous = self.job_phase
        self.job_phase = event
        if (previous is not None and previous.job_id == event.job_id
                and previous.phase is event.phase):
            return
        name = f"작업 {shorten(event.job_id, 8, 4)}"
        if event.phase is JobPhase.MODEL and event.cached is False:
            self._line(f"{name}: 모델을 처음 내려받는 중입니다. "
                       "이 PC에서 처음 쓰는 모델이면 몇 분 걸릴 수 있습니다.")
        elif event.phase is JobPhase.IMAGE:
            self._line(f"{name}: 실행 이미지를 확인하는 중입니다. "
                       "처음이면 내려받는 데 시간이 걸릴 수 있습니다.")
        else:
            self._line(f"{name}: {_PHASE_LABEL[event.phase]}...")

    def _phase_of_job(self) -> JobPhase | None:
        """The phase, only when it belongs to the Job the slot holds."""
        phase = self.job_phase
        if phase is None or self.job.job_id is None or phase.job_id != self.job.job_id:
            return None
        return phase.phase

    def _line(self, text: str) -> None:
        text = redact(text).strip()
        if not text:
            return
        self._lines.append(f"[{self._clock()}] {text}")
        self._written += 1

    def lines_since(self, written: int) -> tuple[list[str], int]:
        """Lines added since the window last asked, and the count to ask with next."""
        new = min(self._written - written, len(self._lines))
        if new <= 0:
            return [], self._written
        return list(self._lines)[-new:], self._written

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    # -- what it adds up to -------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.exit_code is not None

    @property
    def job_busy(self) -> bool:
        return self.job.stage is not JobStage.IDLE

    def display(self) -> Display:
        if self.finished:
            # A run the person stopped ends however it ends -- a sign-in they
            # closed on the way out is still them stopping it, not an error.
            if self.exit_code == 0 or self.stop_mode is not None:
                return Display.STOPPED
            return Display.ERROR
        if self.stop_mode is not None:
            return Display.STOPPING
        if self.stage in (WorkerStage.CONNECTED, WorkerStage.RECONNECTING) and self.job_busy:
            if self.job.stage is JobStage.ACCEPTING:
                return Display.JOB_ASSIGNED
            if (self.job.stage is JobStage.WORKING
                    and self._phase_of_job() in _PREPARING):
                return Display.JOB_ASSIGNED
            return Display.RUNNING
        if self.stage is WorkerStage.CONNECTED:
            return Display.ONLINE
        return Display(self.stage.value)

    def view(self) -> StatusView:
        shown = self.display()
        headline, tone = _HEADLINE[shown]
        if shown is Display.STOPPING and self.stop_mode == "after_work" and self.job_busy:
            headline = "작업을 마친 뒤 종료합니다"
        return StatusView(
            headline=headline, tone=tone,
            gpu=self._gpu_text(), worker=self._worker_text(),
            job=self._job_text(), progress=self._progress_text(),
            button_text=self._button_text(),
            button_enabled=not (self.stop_mode == "now" and not self.finished))

    def _gpu_text(self) -> str:
        if self.gpu is None:
            return "알 수 없음" if self.finished else "확인 중"
        if self.gpu.vram_mb:
            return f"{self.gpu.model} · {self.gpu.vram_mb:,} MB"
        return self.gpu.model

    def _worker_text(self) -> str:
        if self.finished:
            return "오프라인"
        if self.stage is WorkerStage.CONNECTED:
            return f"온라인 · {shorten(self.worker_id)}" if self.worker_id else "온라인"
        if self.stage is WorkerStage.RECONNECTING:
            return "연결 끊김 · 다시 연결하는 중"
        if self.stage is WorkerStage.REGISTRATION_REQUIRED:
            return "등록 필요"
        if self.stage is WorkerStage.CONNECTING:
            return "서버 연결 중"
        return "확인 중"

    def _job_text(self) -> str:
        job = self.job
        if job.stage is JobStage.IDLE:
            if self.last_end is not None:
                return f"대기 중 · 최근 작업 {_ENDED_SHORT[self.last_end.end]}"
            return "대기 중"
        name = f"작업 {shorten(job.job_id or '', 8, 4)}"
        if job.stage is JobStage.ACCEPTING:
            return f"{name} · 작업을 준비하고 있습니다"
        if job.stage is JobStage.CANCELLING:
            return f"{name} · 취소하는 중"
        phase = self._phase_of_job()
        if phase is not None:
            return f"{name} · {_PHASE_LABEL[phase]}"
        return f"{name} · 학습 진행 중"

    def _progress_text(self) -> str:
        if self.job.stage is not JobStage.WORKING or self.job.percent is None:
            return "—"
        if self._phase_of_job() not in (None, JobPhase.TRAINING, JobPhase.SETTLING):
            return "—"
        return f"{self.job.percent:.0f}%"

    def _button_text(self) -> str:
        if self.finished:
            return "닫기"
        if self.stop_mode == "now":
            return "종료 중..."
        if self.stop_mode == "after_work":
            return "지금 종료"
        return "종료"

    # -- closing ------------------------------------------------------------

    def request_close(self, confirm: Callable[[str, str], bool]) -> ShutdownAction:
        """The [종료] button and the window's X. One decision for both.

        ``confirm`` asks the person a yes/no question and is only called when
        there is something to ask: a Job in hand, or a wait for one already
        under way.
        """
        if self.finished:
            return ShutdownAction.CLOSE
        if self.stop_mode == "now":
            return ShutdownAction.IGNORE
        if self.stop_mode == "after_work":
            if not confirm(NOW_TITLE, NOW_QUESTION):
                return ShutdownAction.IGNORE
            self.stop_mode = "now"
            self._line("지금 종료합니다. 진행 중인 작업이 정리되지 않을 수 있습니다.")
            return ShutdownAction.STOP_NOW
        if self.job_busy:
            if not confirm(AFTER_WORK_TITLE, AFTER_WORK_QUESTION):
                return ShutdownAction.IGNORE
            self.stop_mode = "after_work"
            self._line("작업을 마치고 결과를 보낸 뒤 종료합니다. 새 작업은 받지 않습니다.")
            return ShutdownAction.STOP_AFTER_WORK
        self.stop_mode = "now"
        if self.stage in (WorkerStage.CONNECTED, WorkerStage.RECONNECTING):
            self._line("종료하는 중입니다. 연결을 정리한 뒤 창이 닫힙니다.")
        else:
            self._line("종료하는 중입니다. 지금 진행 중인 단계가 끝나는 대로 멈춥니다.")
        return ShutdownAction.STOP_NOW
