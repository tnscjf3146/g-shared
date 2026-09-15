"""The status window: G-shared.exe says what it is doing and can be stopped.

Before this the packaged Worker had no console and no window once it was past
sign-in. A machine that connected fine showed nothing, and the Task Manager was
the only way to stop it.

What is protected here, in the order a supplier meets it:

* the window exists before anything slow starts, and says STARTING;
* what the Worker says reaches the window through a queue, and every Tk-side
  change happens on the window's thread, never a Worker thread;
* ONLINE, a Job being accepted, a Job training at 43% -- each shows up;
* [종료] and the X take the same path, and stopping is the Worker's own stop
  signal, not a kill;
* with a Job in hand, stopping waits for that Job to be reported, and takes no
  new one meanwhile;
* the stale-token recovery still runs, through the window's sign-in;
* nothing secret is shown.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from gshared_worker import auth_check, job_wiring, status_app, status_events, worker_credentials
from gshared_worker import main as worker_main
from gshared_worker.auth_check import authenticate_worker
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.gui.status_presenter import (
    AFTER_WORK_QUESTION,
    NOW_QUESTION,
    Display,
    FailureToShow,
    ShutdownAction,
    SignInRequest,
    StatusPresenter,
    WorkerFinished,
)
from gshared_worker.gui.window import AuthWindow
from gshared_worker.job_acceptance import JobAcceptance
from gshared_worker.job_settlement import JobResultReady
from gshared_worker.runtime_progress import ProgressChannel
from gshared_worker.status_app import StatusApp
from gshared_worker.status_events import (
    GpuDescribed,
    JobChanged,
    JobEnd,
    JobEnded,
    JobStage,
    Said,
    StageChanged,
    StatusQueue,
    WorkerOnline,
    WorkerStage,
    redact,
)
from gshared_worker.worker_connection import (
    InterruptibleStop,
    _dispatch_to,
    job_view,
    send_terminal,
    stay_connected,
)
from gshared_worker.worker_job import JobSlot
from gshared_worker.worker_shutdown import WorkerShutdown

from tests.test_job_progress import Socket, a_progress, assignment, code_only, holding
from tests.test_job_wiring import (
    CREDENTIALS,
    FakeChain,
    FakePipeline,
    FakeSettlement,
    ScriptedBackend,
    configured,
    present,
)
from tests.test_job_wiring import assignment as wire_assignment
from tests.test_worker_token_recovery import (
    ACCEPTED,
    ACCESS_TOKEN,
    DEAD_TOKEN,
    FACTS,
    INVALIDATED,
    ISSUED,
    SETTINGS,
    WALLET,
    WALLET_FILE,
    Registry,
    StageTestCase,
    measured,
)

GPU_FACTS = SimpleNamespace(gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU", vram_mb=8188)


class Recorded:
    def __init__(self):
        self.events: list = []
        self.lock = threading.Lock()

    def publish(self, event):
        with self.lock:
            self.events.append(event)

    def of(self, kind):
        with self.lock:
            return [e for e in self.events if isinstance(e, kind)]


def watching(test: unittest.TestCase) -> Recorded:
    sink = Recorded()
    previous = status_events.install(sink)
    test.addCleanup(status_events.install, previous)
    return sink


def say_nothing(*parts):
    return None


# --------------------------------------------------------------------------
# A window without Tk, driven on the test's own thread
# --------------------------------------------------------------------------

class FakeWindow:
    """What StatusWindow does, minus drawing: pump, apply, call back, close."""

    def __init__(self, presenter, feed, on_action, on_event, *, on_tick=None,
                 confirm=None):
        self.presenter = presenter
        self.feed = feed
        self.on_action = on_action
        self.on_event = on_event
        self.on_tick = on_tick
        self.confirm = confirm or (lambda title, question: True)
        self.closed = False
        self.errors: list[str] = []
        self.views: list = []
        self.applied: list = []
        self.threads: set[int] = set()
        self._callbacks: list = []
        self.opened_at = time.monotonic()

    def run(self):
        deadline = time.monotonic() + 15
        while not self.closed and time.monotonic() < deadline:
            for event in self.feed.drain():
                self.threads.add(threading.get_ident())
                self.presenter.apply(event)
                self.applied.append(event)
                self.on_event(event)
                self.views.append(self.presenter.view())
            while self._callbacks and not self.closed:
                self._callbacks.pop(0)()
            if self.on_tick is not None and not self.closed:
                self.on_tick(self)
            time.sleep(0.01)

    def later(self, milliseconds, callback):
        self._callbacks.append(callback)

    def close(self):
        self.closed = True

    def show_error(self, text):
        self.errors.append(text)

    def close_requested(self):
        self.threads.add(threading.get_ident())
        self.on_action(self.presenter.request_close(self.confirm))
        self.views.append(self.presenter.view())


def app_with(run, **window_options):
    made: dict = {}

    def make_window(presenter, feed, on_action, on_event):
        made["window"] = FakeWindow(presenter, feed, on_action, on_event,
                                    **window_options)
        return made["window"]

    app = StatusApp(run=run, make_window=make_window, describe=lambda: GPU_FACTS)
    return app, made


def close_once_finished(window):
    if window.presenter.finished:
        window.close_requested()


# --------------------------------------------------------------------------
# Case 1 and 2: the window first, then what the Worker says, on the right thread
# --------------------------------------------------------------------------

class Case1TheWindowComesFirstTest(unittest.TestCase):
    def setUp(self):
        self.order: list[str] = []

        def run(**kwargs):
            self.order.append("worker started")
            return int(WorkerExitCode.OK)

        def make_window(presenter, feed, on_action, on_event):
            self.order.append("window made")
            self.window = FakeWindow(presenter, feed, on_action, on_event,
                                     on_tick=close_once_finished)
            return self.window

        self.app = StatusApp(run=run, make_window=make_window,
                             describe=lambda: GPU_FACTS)
        self.code = self.app.run()

    def test_the_window_is_made_before_the_worker_starts(self):
        self.assertEqual(self.order, ["window made", "worker started"])

    def test_the_first_thing_it_shows_is_starting(self):
        self.assertEqual(self.window.applied[0], StageChanged(WorkerStage.STARTING))
        self.assertEqual(self.window.views[0].headline, "시작하는 중")

    def test_the_gpu_is_shown_from_the_machines_own_description(self):
        self.assertIn(GpuDescribed(GPU_FACTS.gpu_model, 8188), self.window.applied)
        self.assertEqual(self.app.presenter.view().gpu,
                         "NVIDIA GeForce RTX 4070 Laptop GPU · 8,188 MB")

    def test_a_worker_that_ended_cleanly_leaves_a_closeable_window(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertTrue(self.window.closed)

    def test_the_status_bus_is_put_back_afterwards(self):
        self.assertNotIsInstance(status_events._sink, StatusQueue)


class Case2ProgressCrossesThroughTheQueueTest(unittest.TestCase):
    def setUp(self):
        self.worker_threads: set[int] = set()

        def run(*, out, stop, open_sign_in, show_failure):
            self.worker_threads.add(threading.get_ident())
            out("")
            out("시스템 환경을 확인하는 중...")
            status_events.publish(StageChanged(WorkerStage.ENVIRONMENT_CHECK))
            out("  GPU 환경이 준비되었습니다.")
            return int(WorkerExitCode.OK)

        self.app, self.made = app_with(run, on_tick=close_once_finished)
        self.main_thread = threading.get_ident()
        self.app.run()
        self.window = self.made["window"]

    def test_the_worker_ran_on_a_thread_of_its_own(self):
        self.assertEqual(len(self.worker_threads), 1)
        self.assertNotIn(self.main_thread, self.worker_threads)

    def test_every_event_was_applied_on_the_windows_thread(self):
        self.assertEqual(self.window.threads, {self.main_thread})

    def test_the_existing_lines_are_what_the_window_shows(self):
        lines = "\n".join(self.app.presenter.lines)
        self.assertIn("시스템 환경을 확인하는 중...", lines)
        self.assertIn("GPU 환경이 준비되었습니다.", lines)

    def test_the_stage_is_shown(self):
        self.assertIn("시스템 환경 확인 중", [v.headline for v in self.window.views])

    def test_a_blank_line_is_not_a_log_entry(self):
        self.assertTrue(all(line.split("] ", 1)[1] for line in self.app.presenter.lines))


class TheQueueItselfTest(unittest.TestCase):
    def test_events_from_many_threads_arrive_whole_and_in_each_threads_order(self):
        feed = StatusQueue()

        def speak(name):
            for n in range(200):
                feed.publish(Said(f"{name}-{n}"))

        threads = [threading.Thread(target=speak, args=(name,)) for name in "abc"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        drained = []
        while True:
            taken = feed.drain(limit=100)
            if not taken:
                break
            drained.extend(taken)
        self.assertEqual(len(drained), 600)
        for name in "abc":
            mine = [int(e.text.split("-")[1]) for e in drained if e.text.startswith(name)]
            self.assertEqual(mine, list(range(200)))

    def test_publishing_with_nobody_watching_costs_nothing_and_never_raises(self):
        previous = status_events.install(None)
        self.addCleanup(status_events.install, previous)
        status_events.publish(Said("anything"))

    def test_a_watcher_that_breaks_does_not_break_the_worker(self):
        class Broken:
            def publish(self, event):
                raise RuntimeError("the window went away")

        previous = status_events.install(Broken())
        self.addCleanup(status_events.install, previous)
        status_events.publish(Said("still fine"))


# --------------------------------------------------------------------------
# Case 3: ONLINE, from the real connection loop
# --------------------------------------------------------------------------

class UntilOnline:
    def __init__(self, sink):
        self._sink = sink
        self._deadline = time.monotonic() + 10

    def is_set(self):
        return (StageChanged(WorkerStage.CONNECTED) in self._sink.of(StageChanged)
                or time.monotonic() > self._deadline)

    def wait(self, timeout=None):
        time.sleep(min(timeout or 0, 0.05))
        return self.is_set()


class Case3OnlineIsShownTest(unittest.TestCase):
    def setUp(self):
        self.sink = watching(self)
        backend = ScriptedBackend(wire_assignment())
        backend._pending.clear()
        self.code = stay_connected(
            configured(escrow_address=None), CREDENTIALS, say_nothing,
            UntilOnline(self.sink), open_socket=lambda url, token: backend,
            interval=0.2, ack_timeout=5)

    def test_the_connection_says_it_is_online_as_which_worker(self):
        self.assertIn(WorkerOnline("worker-001"), self.sink.events)
        self.assertIn(StageChanged(WorkerStage.CONNECTED), self.sink.events)

    def test_the_window_shows_online_and_waiting(self):
        presenter = StatusPresenter()
        for event in self.sink.events:
            presenter.apply(event)
        view = presenter.view()

        self.assertIs(presenter.display(), Display.ONLINE)
        self.assertEqual(view.headline, "온라인")
        self.assertEqual(view.worker, "온라인 · worker-001")
        self.assertEqual(view.job, "대기 중")

    def test_a_long_worker_id_is_shortened(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.CONNECTED))
        presenter.apply(WorkerOnline("worker-1b767d99-10a2-4af3-8017-baf8f7b2903f"))

        self.assertEqual(presenter.view().worker, "온라인 · worker-1b76…903f")


# --------------------------------------------------------------------------
# Case 4 and 5: the Job, and how far it has got
# --------------------------------------------------------------------------

class Case4And5TheJobAndItsProgressTest(unittest.TestCase):
    def test_an_offered_job_being_accepted(self):
        view = job_view(holding(confirmed=False), JobAcceptance())

        self.assertEqual(view, JobChanged("123", JobStage.ACCEPTING))

    def test_an_accepted_job_with_no_progress_yet(self):
        self.assertEqual(job_view(holding(), JobAcceptance()),
                         JobChanged("123", JobStage.WORKING, None))

    def test_an_empty_slot(self):
        self.assertEqual(job_view(JobSlot(), JobAcceptance()),
                         JobChanged(None, JobStage.IDLE))

    def test_progress_is_read_without_taking_it_from_the_backend(self):
        acceptance = JobAcceptance()
        generation = acceptance.progress.next_generation()
        acceptance.progress.publish(a_progress(generation=generation, percent=43.0))

        view = job_view(holding(), acceptance)

        self.assertEqual(view.percent, 43.0)
        # Still there for send_progress: the window peeked, it did not take.
        self.assertIsNotNone(acceptance.progress.take())

    def test_another_jobs_progress_is_not_shown_for_this_one(self):
        acceptance = JobAcceptance()
        generation = acceptance.progress.next_generation()
        acceptance.progress.publish(a_progress(job_id="999", generation=generation))

        self.assertIsNone(job_view(holding(), acceptance).percent)

    def test_a_customer_cancellation_under_way(self):
        acceptance = SimpleNamespace(
            terminal_for=SimpleNamespace(job_id="123", cancel_requested=True),
            progress=ProgressChannel())

        self.assertEqual(job_view(holding(), acceptance).stage, JobStage.CANCELLING)

    def test_what_the_window_shows_for_each(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.CONNECTED))

        presenter.apply(JobChanged("123", JobStage.ACCEPTING))
        self.assertIs(presenter.display(), Display.JOB_ASSIGNED)
        self.assertIn("작업을 준비하고 있습니다", presenter.view().job)

        presenter.apply(JobChanged("123", JobStage.WORKING, 43.0))
        self.assertIs(presenter.display(), Display.RUNNING)
        self.assertEqual(presenter.view().headline, "작업 실행 중")
        self.assertIn("학습 진행 중", presenter.view().job)
        self.assertEqual(presenter.view().progress, "43%")

    def test_each_ending_is_said_and_the_slot_shows_waiting_again(self):
        for end, line in ((JobEnd.COMPLETED, "작업이 완료되었습니다."),
                          (JobEnd.FAILED, "작업 실행에 실패했습니다."),
                          (JobEnd.CANCELLED, "작업이 취소되었습니다.")):
            with self.subTest(end=end):
                presenter = StatusPresenter()
                presenter.apply(StageChanged(WorkerStage.CONNECTED))
                presenter.apply(JobChanged("123", JobStage.WORKING, 99.0))
                presenter.apply(JobEnded("123", end))

                self.assertIs(presenter.display(), Display.ONLINE)
                self.assertTrue(presenter.view().job.startswith("대기 중"))
                self.assertIn(line, presenter.lines[-1])

    def test_the_log_keeps_only_the_last_lines(self):
        presenter = StatusPresenter()
        for n in range(StatusPresenter.MAX_LINES + 50):
            presenter.apply(Said(f"line {n}"))

        self.assertEqual(len(presenter.lines), StatusPresenter.MAX_LINES)
        self.assertIn(f"line {StatusPresenter.MAX_LINES + 49}", presenter.lines[-1])
        new, written = presenter.lines_since(StatusPresenter.MAX_LINES + 45)
        self.assertEqual(len(new), 5)
        self.assertEqual(written, StatusPresenter.MAX_LINES + 50)

    def test_a_terminal_answer_that_went_out_is_published_as_the_jobs_end(self):
        sink = watching(self)
        acceptance = JobAcceptance()
        acceptance.terminal.put(JobResultReady(
            job_id="123", object_key="jobs/123/result.tar",
            result_hash="0x" + "5e" * 32, submit_tx_hash="0x" + "ab" * 32,
            actual_runtime_sec=12.5))

        self.assertTrue(send_terminal(acceptance, Socket(), holding()))
        self.assertEqual(sink.of(JobEnded), [JobEnded("123", JobEnd.COMPLETED)])


# --------------------------------------------------------------------------
# Case 6 and 7: [종료] and X, one path, the Worker's own stop signal
# --------------------------------------------------------------------------

class Case6TheStopButtonTest(unittest.TestCase):
    def setUp(self):
        self.stopped_by: list = []
        self.released = threading.Event()

        def run(*, out, stop, open_sign_in, show_failure):
            status_events.publish(StageChanged(WorkerStage.CONNECTED))
            out("  연결되었습니다. 이 PC가 서비스에 등록되어 있습니다.")
            while not stop.wait(0.05):
                pass
            self.stopped_by.append(stop)
            out("G-shared 서비스와 연결이 끊겼습니다.")
            self.released.set()
            return int(WorkerExitCode.OK)

        def press_stop_once_online(window):
            if (window.presenter.stage is WorkerStage.CONNECTED
                    and window.presenter.stop_mode is None):
                window.close_requested()

        self.app, self.made = app_with(run, on_tick=press_stop_once_online)
        self.code = self.app.run()
        self.window = self.made["window"]

    def test_the_workers_stop_signal_is_what_was_set(self):
        self.assertEqual(self.stopped_by, [self.app.shutdown])
        self.assertIsInstance(self.app.shutdown, InterruptibleStop)
        self.assertTrue(self.app.shutdown.requested_now)

    def test_it_says_stopping_and_the_button_cannot_be_pressed_again(self):
        stopping = [v for v in self.window.views if v.headline == "종료 중"]
        self.assertTrue(stopping)
        self.assertFalse(stopping[0].button_enabled)
        self.assertEqual(stopping[0].button_text, "종료 중...")

    def test_the_worker_finished_its_cleanup_before_the_window_closed(self):
        self.assertTrue(self.released.is_set())
        self.assertTrue(self.window.closed)
        self.assertEqual(self.code, WorkerExitCode.OK)

    def test_a_stopped_run_ends_as_stopped_not_as_an_error(self):
        self.assertIs(self.app.presenter.display(), Display.STOPPED)
        self.assertEqual(self.window.errors, [])


class TheRequestCloseDecisionTest(unittest.TestCase):
    """The one decision behind both [종료] and X."""

    def asked(self):
        questions: list[str] = []

        def confirm(title, question):
            questions.append(question)
            return self.answer

        return questions, confirm

    def setUp(self):
        self.answer = True

    def test_with_nothing_in_hand_it_stops_without_asking(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.CONNECTED))
        questions, confirm = self.asked()

        self.assertIs(presenter.request_close(confirm), ShutdownAction.STOP_NOW)
        self.assertEqual(questions, [])
        self.assertIs(presenter.display(), Display.STOPPING)

    def test_pressing_again_while_stopping_does_nothing(self):
        presenter = StatusPresenter()
        presenter.request_close(lambda t, q: True)

        self.assertIs(presenter.request_close(lambda t, q: True), ShutdownAction.IGNORE)

    def test_once_finished_it_closes(self):
        presenter = StatusPresenter()
        presenter.apply(WorkerFinished(2))

        self.assertIs(presenter.request_close(lambda t, q: False), ShutdownAction.CLOSE)

    def test_during_startup_it_says_the_stage_will_finish_first(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.RUNTIME_PREPARING))
        presenter.request_close(lambda t, q: True)

        self.assertIn("진행 중인 단계가 끝나는 대로", presenter.lines[-1])


@unittest.skipUnless(hasattr(__import__("tkinter"), "Tk"), "tkinter is not available")
class Case7TheXAndTheButtonAreOnePathTest(unittest.TestCase):
    """The real window, opened and closed here where there is a display."""

    def setUp(self):
        import tkinter as tk

        try:
            probe = tk.Tk()
            probe.destroy()
        except tk.TclError as error:
            self.skipTest(f"no display: {error}")

    def test_both_call_the_same_decision(self):
        from gshared_worker.gui.status_window import StatusWindow

        decided: list[str] = []

        class Counting(StatusPresenter):
            def request_close(self, confirm):
                decided.append("asked")
                return super().request_close(confirm)

        actions: list = []
        window = StatusWindow(Counting(), StatusQueue(), actions.append).open()
        self.addCleanup(window.close)
        window._presenter.apply(WorkerFinished(0))

        window._button.invoke()
        window._root.tk.call(window._root.protocol("WM_DELETE_WINDOW"))

        self.assertEqual(decided, ["asked", "asked"])
        self.assertEqual(actions, [ShutdownAction.CLOSE, ShutdownAction.CLOSE])

    def test_the_log_area_keeps_only_the_last_lines(self):
        from gshared_worker.gui.status_window import StatusWindow

        presenter = StatusPresenter()
        feed = StatusQueue()
        window = StatusWindow(presenter, feed, lambda action: None).open()
        self.addCleanup(window.close)
        for n in range(StatusPresenter.MAX_LINES + 30):
            presenter.apply(Said(f"line {n}"))
            if n % 40 == 0:
                window._render()
        window._render()

        text = window._log.get("1.0", "end-1c").splitlines()
        self.assertEqual(len(text), StatusPresenter.MAX_LINES)
        self.assertIn(f"line {StatusPresenter.MAX_LINES + 29}", text[-1])

    def test_sign_in_opened_from_the_window_can_be_closed_by_stopping(self):
        from gshared_worker.gui.presenter import AuthPresenter
        from gshared_worker.gui.status_window import StatusWindow

        from tests.test_auth_gui import Backend

        app = StatusApp()
        app.window = window = StatusWindow(
            app.presenter, app.feed, app.act, app.on_event).open()
        request = SignInRequest(AuthWindow(AuthPresenter(Backend())))
        window.later(50, lambda: app._open_sign_in(request))
        window.later(600, lambda: app.act(ShutdownAction.STOP_NOW))
        still_running: list[bool] = []
        window.later(900, lambda: still_running.append(not window.closed))
        window.later(1000, window.close)
        window.later(8000, window.close)   # a hang is a failure, not a stuck run

        window.run()

        self.assertTrue(request.done.is_set())
        self.assertIsNone(request.result)
        # The sign-in window's own loop ended without ending the status window's.
        self.assertEqual(still_running, [True])


# --------------------------------------------------------------------------
# Case 8: a Job in hand -- finish it, report it, then stop; never a kill
# --------------------------------------------------------------------------

class TheShutdownSignalTest(unittest.TestCase):
    def test_after_work_with_nothing_held_is_a_stop(self):
        shutdown = WorkerShutdown()
        shutdown.watch_work(lambda: False)
        shutdown.request_after_work()

        self.assertTrue(shutdown.is_set())

    def test_after_work_waits_while_a_job_is_held(self):
        busy = [True]
        shutdown = WorkerShutdown()
        shutdown.watch_work(lambda: busy[0])
        shutdown.request_after_work()

        self.assertFalse(shutdown.is_set())
        self.assertFalse(shutdown.accepting_work())
        busy[0] = False
        self.assertTrue(shutdown.is_set())

    def test_a_wait_notices_the_job_ending_without_anybody_setting_it(self):
        busy = [True]
        shutdown = WorkerShutdown()
        shutdown.watch_work(lambda: busy[0])
        shutdown.request_after_work()
        threading.Timer(0.2, lambda: busy.__setitem__(0, False)).start()

        started = time.monotonic()
        self.assertTrue(shutdown.wait(10))
        self.assertLess(time.monotonic() - started, 3)

    def test_now_is_now_whatever_is_held(self):
        shutdown = WorkerShutdown()
        shutdown.watch_work(lambda: True)
        shutdown.request_now()

        self.assertTrue(shutdown.is_set())

    def test_not_knowing_whether_a_job_is_held_is_not_idle(self):
        def cannot_tell():
            raise RuntimeError("slot unavailable")

        shutdown = WorkerShutdown()
        shutdown.watch_work(cannot_tell)
        shutdown.request_after_work()

        self.assertFalse(shutdown.is_set())


class Case8StoppingWithAJobInHandTest(unittest.TestCase):
    def test_the_person_is_asked_and_saying_yes_waits_for_the_job(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.CONNECTED))
        presenter.apply(JobChanged("123", JobStage.WORKING, 20.0))
        questions: list[str] = []

        action = presenter.request_close(
            lambda title, question: questions.append(question) or True)

        self.assertIs(action, ShutdownAction.STOP_AFTER_WORK)
        self.assertEqual(questions, [AFTER_WORK_QUESTION])
        self.assertEqual(presenter.view().headline, "작업을 마친 뒤 종료합니다")

    def test_saying_no_keeps_it_running(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.CONNECTED))
        presenter.apply(JobChanged("123", JobStage.WORKING, 20.0))

        self.assertIs(presenter.request_close(lambda t, q: False), ShutdownAction.IGNORE)
        self.assertIsNone(presenter.stop_mode)

    def test_stopping_at_once_while_waiting_is_a_second_explicit_question(self):
        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.CONNECTED))
        presenter.apply(JobChanged("123", JobStage.WORKING, 20.0))
        presenter.request_close(lambda t, q: True)
        questions: list[str] = []

        action = presenter.request_close(
            lambda title, question: questions.append(question) or True)

        self.assertIs(action, ShutdownAction.STOP_NOW)
        self.assertEqual(questions, [NOW_QUESTION])

    def test_the_app_turns_each_answer_into_the_workers_stop_signal(self):
        app = StatusApp()
        app.act(ShutdownAction.STOP_AFTER_WORK)
        self.assertTrue(app.shutdown.requested)
        self.assertFalse(app.shutdown.requested_now)

        app.act(ShutdownAction.STOP_NOW)
        self.assertTrue(app.shutdown.requested_now)

    def test_no_kill_or_exit_anywhere_in_the_stopping_code(self):
        from gshared_worker import worker_shutdown
        from gshared_worker.gui import status_presenter, status_window

        for module in (status_app, worker_shutdown, status_window, status_presenter):
            code = code_only(module)
            for forbidden in ("os._exit", "sys.exit", ".kill(", ".terminate(",
                              "TerminateProcess", "taskkill"):
                with self.subTest(module=module.__name__, forbidden=forbidden):
                    self.assertNotIn(forbidden, code)

    def test_a_new_assignment_is_not_taken_while_stopping(self):
        decided: list = []
        acceptance = JobAcceptance(decide=lambda job: decided.append(job))
        slot = JobSlot()
        said: list[str] = []

        _dispatch_to(Socket(), slot, lambda *p: said.append(" ".join(map(str, p))),
                     acceptance, accepting_work=lambda: False)(assignment())

        self.assertIsNone(slot.held)
        self.assertFalse(acceptance.busy)
        self.assertIn("새 작업을 받지 않았습니다", "\n".join(said))

    def test_the_job_already_held_still_gets_its_reply_while_stopping(self):
        socket = Socket()
        slot = holding()

        _dispatch_to(socket, slot, say_nothing, JobAcceptance(),
                     accepting_work=lambda: False)(assignment())

        self.assertEqual([m["type"] for m in socket.sent], ["JOB_ACCEPTED"])


class GatedPipeline(FakePipeline):
    """Trains until the test lets it finish."""

    gate = threading.Event()

    def run(self, job, *, progress=None, terminal=None):
        GatedPipeline.gate.wait(20)
        return super().run(job, progress=progress, terminal=terminal)


class Case8TheRealLoopFinishesTheJobThenStopsTest(unittest.TestCase):
    """JOB_ASSIGN -> accepted -> stop asked -> still reports -> then stops."""

    def setUp(self):
        GatedPipeline.gate = threading.Event()
        FakePipeline.built.clear()
        FakeSettlement.built.clear()
        self.sink = watching(self)
        self.chain = FakeChain()
        self.folder = tempfile.mkdtemp()
        for patcher in (
            patch.object(job_wiring, "load_wallet", present),
            patch.object(job_wiring, "Web3Escrow", lambda settings, wallet: self.chain),
            patch.object(job_wiring, "RuntimePipeline", GatedPipeline),
            patch.object(job_wiring, "Settlement", FakeSettlement),
            patch.object(job_wiring, "WslCommandRunner", lambda: object()),
            patch.object(job_wiring, "BackendClient", lambda origin: object()),
            patch.object(job_wiring, "data_directory", lambda: Path(self.folder)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(GatedPipeline.gate.set)

        self.backend = ScriptedBackend(wire_assignment())
        self.shutdown = WorkerShutdown()
        self.result: list[int] = []
        loop = threading.Thread(target=lambda: self.result.append(stay_connected(
            configured(), CREDENTIALS, say_nothing, self.shutdown,
            open_socket=lambda url, token: self.backend,
            interval=0.2, ack_timeout=5)))
        loop.start()

        self.assertTrue(self.wait_for(lambda: "JOB_ACCEPTED" in self.backend.types()))
        self.shutdown.request_after_work()
        # A second offer arrives while stopping. It must not be taken.
        self.backend._pending.append(wire_assignment("124"))
        time.sleep(0.6)
        self.still_running_while_held = loop.is_alive()
        GatedPipeline.gate.set()
        loop.join(30)
        self.loop = loop

    def wait_for(self, condition, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return False

    def test_it_did_not_stop_while_the_job_was_held(self):
        self.assertTrue(self.still_running_while_held)

    def test_the_job_was_reported_before_it_stopped(self):
        self.assertFalse(self.loop.is_alive())
        self.assertIn("JOB_RESULT", self.backend.types())
        self.assertEqual(self.result, [int(WorkerExitCode.OK)])

    def test_nothing_new_was_accepted_meanwhile(self):
        self.assertEqual([m["jobId"] for m in self.backend.sent
                          if m["type"] == "JOB_ACCEPTED"], ["123"])
        self.assertEqual(self.chain.sent, [("accept", 17)])

    def test_the_window_was_told_about_the_job_all_the_way_through(self):
        stages = [e.stage for e in self.sink.of(JobChanged)]
        self.assertIn(JobStage.WORKING, stages)
        self.assertIn(JobEnded("123", JobEnd.COMPLETED), self.sink.events)


# --------------------------------------------------------------------------
# Startup stages: published in order, stoppable between them
# --------------------------------------------------------------------------

class TheStagesTest(unittest.TestCase):
    def setUp(self):
        self.sink = watching(self)
        self.reached: list[tuple] = []

    def stage(self, name, code=0):
        def run(settings, say, **given):
            self.reached.append((name, given))
            return code
        return run

    def start(self, stop=None, open_sign_in=None):
        with patch.object(worker_main, "check_environment", self.stage("environment")), \
                patch.object(worker_main, "ensure_runtime", self.stage("runtime")), \
                patch.object(worker_main, "authenticate_worker", self.stage("auth")), \
                patch.object(worker_main, "connect_to_backend", self.stage("connect")):
            return worker_main.start_the_machine(
                SETTINGS, say_nothing, stop=stop, open_sign_in=open_sign_in)

    def test_each_stage_is_published_before_it_runs(self):
        self.start()

        self.assertEqual([e.stage for e in self.sink.of(StageChanged)], [
            WorkerStage.ENVIRONMENT_CHECK, WorkerStage.RUNTIME_PREPARING,
            WorkerStage.AUTHENTICATING, WorkerStage.CONNECTING])

    def test_the_window_brings_its_stop_and_its_sign_in_to_the_stages_that_use_them(self):
        shutdown = WorkerShutdown()

        def sign_in(window):
            return None

        self.start(stop=shutdown, open_sign_in=sign_in)

        given = dict(self.reached)
        self.assertEqual(given["environment"], {})
        self.assertEqual(given["runtime"], {})
        self.assertEqual(given["auth"], {"open_sign_in": sign_in})
        self.assertEqual(given["connect"], {"stop": shutdown})

    def test_a_stop_asked_for_during_a_stage_is_honoured_before_the_next(self):
        shutdown = WorkerShutdown()

        def environment(settings, say, **given):
            self.reached.append(("environment", given))
            shutdown.request_now()
            return 0

        said: list[str] = []
        with patch.object(worker_main, "check_environment", environment), \
                patch.object(worker_main, "ensure_runtime", self.stage("runtime")):
            code = worker_main.start_the_machine(
                SETTINGS, lambda *p: said.append(" ".join(map(str, p))), stop=shutdown)

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual([name for name, _ in self.reached], ["environment"])
        self.assertIn("종료 요청에 따라 G-shared를 멈췄습니다.", said)

    def test_from_a_terminal_nothing_extra_is_passed(self):
        self.start()

        self.assertTrue(all(given == {} for _, given in self.reached))


class ALaunchFromATerminalIsUnchangedTest(unittest.TestCase):
    def test_run_from_source_no_window_is_opened(self):
        ran: list = []
        with patch.object(status_app.sys, "frozen", False, create=True), \
                patch.object(worker_main, "run", lambda: ran.append(1) or 0), \
                patch.object(status_app, "StatusApp",
                             lambda: self.fail("a window was opened")):
            self.assertEqual(status_app.launch(), 0)
        self.assertEqual(ran, [1])

    def test_the_packaged_worker_opens_the_window_even_with_handles_inherited(self):
        # Found by the packaged smoke test: started with a parent's stdout, the
        # exe decided it had a console and ran with no window at all.
        opened: list = []

        class App:
            def run(self):
                opened.append(1)
                return 0

        with patch.object(status_app.sys, "frozen", True, create=True), \
                patch.object(status_app.sys, "stdout", object()), \
                patch.object(status_app, "StatusApp", App), \
                patch.object(worker_main, "run",
                             lambda: self.fail("ran without the window")):
            self.assertEqual(status_app.launch(), 0)
        self.assertEqual(opened, [1])


class AFailureIsShownInTheWindowTest(unittest.TestCase):
    def test_the_same_text_goes_to_an_error_dialog_on_the_windows_thread(self):
        def run(*, out, stop, open_sign_in, show_failure):
            out("이 PC는 아직 GPU 작업을 실행할 준비가 되지 않았습니다.")
            show_failure(2, ["이 PC는 아직 GPU 작업을 실행할 준비가 되지 않았습니다."])
            return 2

        ticks = [0]

        def close_after_a_while(window):
            if window.presenter.finished:
                ticks[0] += 1
                if ticks[0] > 5:
                    window.close_requested()

        app, made = app_with(run, on_tick=close_after_a_while)
        code = app.run()
        window = made["window"]

        self.assertEqual(code, 2)
        self.assertEqual(len(window.errors), 1)
        self.assertIn("준비가 되지 않았습니다", window.errors[0])
        self.assertIn("오류", [v.headline for v in window.views])

    def test_a_run_the_person_stopped_does_not_show_an_error(self):
        shown: list = []
        app = StatusApp()
        app.shutdown.request_now()
        with patch.object(status_events, "publish", shown.append):
            app._failure(2, ["로그인을 취소해 이 PC는 아직 등록되지 않았습니다."])

        self.assertEqual(shown, [])


class SignInRunsOnTheWindowsThreadTest(unittest.TestCase):
    def test_the_worker_thread_waits_and_gets_the_windows_answer(self):
        self.asked_on: list[int] = []
        answers: list = []

        class Window:
            def ask_to_sign_in(inner):
                self.asked_on.append(threading.get_ident())
                return "user-access-jwt"

            def close(inner):
                pass

        def run(*, out, stop, open_sign_in, show_failure):
            answers.append(open_sign_in(Window()))
            return 0

        app, made = app_with(run, on_tick=close_once_finished)
        app.run()

        self.assertEqual(answers, ["user-access-jwt"])
        self.assertEqual(self.asked_on, [threading.get_ident()])
        # The access token is not in the request's repr.
        self.assertNotIn("user-access-jwt", repr(SignInRequest(Window(), result="user-access-jwt")))


# --------------------------------------------------------------------------
# Case 9: the stale-token recovery, through the window's sign-in
# --------------------------------------------------------------------------

class Case9RecoveryThroughTheWindowTest(StageTestCase):
    def setUp(self):
        super().setUp()
        self.sink = watching(self)
        self.windows: list = []

        def open_sign_in(window):
            self.windows.append(window)
            return ACCESS_TOKEN

        self.registry = Registry([INVALIDATED, ACCEPTED])
        self.code = authenticate_worker(
            SETTINGS, self.said.append, registry=self.registry,
            describe=lambda: FACTS, benchmark=measured,
            make_wallet=self.make_wallet, open_sign_in=open_sign_in)

    def test_it_registers_again_as_before(self):
        self.assertEqual(self.code, WorkerExitCode.OK)
        self.assertEqual(self.written()["workerId"], ISSUED.worker_id)
        self.assertEqual(len(self.registry.registered), 1)

    def test_the_real_sign_in_window_was_handed_over_unopened(self):
        self.assertEqual(len(self.windows), 1)
        self.assertIsInstance(self.windows[0], AuthWindow)
        self.assertIsNone(self.windows[0]._root)

    def test_the_operator_key_is_kept(self):
        self.assertEqual(self.wallet_file.read_text(encoding="utf-8"), WALLET_FILE)
        _, registration = self.registry.registered[0]
        self.assertEqual(registration.as_payload()["operatorAddress"], WALLET.address)

    def test_the_window_says_registration_is_needed_then_goes_on(self):
        stages = [e.stage for e in self.sink.of(StageChanged)]
        self.assertEqual(stages, [WorkerStage.REGISTRATION_REQUIRED,
                                  WorkerStage.AUTHENTICATING])

        presenter = StatusPresenter()
        presenter.apply(StageChanged(WorkerStage.REGISTRATION_REQUIRED))
        self.assertEqual(presenter.view().headline, "Worker 등록이 필요합니다")
        for line in self.said:
            presenter.apply(Said(line))
        self.assertIn("기존 Worker 등록 정보가 만료되었습니다.", "\n".join(presenter.lines))

    def test_no_token_reaches_the_window(self):
        presenter = StatusPresenter()
        for line in self.said:
            presenter.apply(Said(line))
        for event in self.sink.events:
            presenter.apply(event)
        shown = "\n".join(presenter.lines) + repr(presenter.view())
        for secret in (DEAD_TOKEN, ACCESS_TOKEN, ISSUED.worker_token):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, shown)
                self.assertNotIn(secret, repr(self.sink.events))

    def test_closing_the_sign_in_window_still_ends_as_it_always_did(self):
        self.config.write_text(json.dumps({
            "workerId": "worker-001", "workerTokenProtected": "sealed:" + DEAD_TOKEN,
            "hourlyPrice": 900, "gpuIndex": 0}), encoding="utf-8")

        code = authenticate_worker(
            SETTINGS, self.said.append, registry=Registry([INVALIDATED]),
            describe=lambda: FACTS, benchmark=measured,
            make_wallet=self.make_wallet, open_sign_in=lambda window: None)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertNotIn("workerId", self.written())


class Case9TheOldSignInPathIsUntouchedTest(unittest.TestCase):
    def test_without_the_window_the_sign_in_window_opens_where_it_always_did(self):
        opened: list = []

        class Window:
            def __init__(self, presenter):
                pass

            def ask_to_sign_in(self):
                opened.append(threading.get_ident())
                return None

        with patch.object(auth_check, "AuthWindow", Window):
            auth_check._open_sign_in_window(SETTINGS)()

        self.assertEqual(opened, [threading.get_ident()])


# --------------------------------------------------------------------------
# Case 10: nothing secret on screen
# --------------------------------------------------------------------------

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdXBwbGllciJ9.Q2hhbmdlTWVQbGVhc2U"
PRIVATE_KEY = "0x" + "4f" * 32
SIGNED_QUERY = "X-Amz-Signature=deadbeefcafe&X-Amz-Credential=AKIAEXAMPLE"
WORKER_TOKEN = "gsh_wk_" + "Zq7" * 14
ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


class Case10NothingSecretIsShownTest(unittest.TestCase):
    def setUp(self):
        self.presenter = StatusPresenter()
        for text in (
            f"로그인 응답: {JWT}",
            f"Authorization: Bearer {WORKER_TOKEN}",
            f"서명 키 {PRIVATE_KEY}",
            f"데이터셋 https://storage.example.com/ds/42.jsonl?{SIGNED_QUERY}",
            f"토큰 {WORKER_TOKEN}",
            f"운영 주소 {ADDRESS}",
        ):
            self.presenter.apply(Said(text))
        self.presenter.apply(FailureToShow(f"실패: {JWT} {PRIVATE_KEY}"))
        self.shown = "\n".join(self.presenter.lines) + "\n" + (self.presenter.failure or "")

    def test_no_secret_survives(self):
        for secret in (JWT, PRIVATE_KEY, "4f" * 32, SIGNED_QUERY, "deadbeefcafe",
                       WORKER_TOKEN, "Zq7Zq7Zq7"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, self.shown)

    def test_an_address_is_shortened_not_erased(self):
        self.assertIn("0x7099…79C8", self.shown)
        self.assertNotIn(ADDRESS, self.shown)

    def test_a_signed_url_keeps_where_it_points_but_not_its_signature(self):
        self.assertIn("https://storage.example.com/ds/42.jsonl?", self.shown)

    def test_ordinary_lines_are_left_as_they_are(self):
        line = "  작업 123: 학습을 준비하는 중..."
        self.assertEqual(redact(line), line)
        self.assertEqual(redact("G-shared 서비스에 연결하는 중..."),
                         "G-shared 서비스에 연결하는 중...")


if __name__ == "__main__":
    unittest.main()
