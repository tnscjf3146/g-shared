"""What a step event has to survive before it becomes a percentage.

The container publishes numbers. Some of them will be wrong -- a duplicated
line, a step past the total, a clock that went sideways -- and the Backend
stores whatever arrives against a customer's Job. So every rule that decides
whether an event is publishable lives in one place and is checked here.

The rule that runs through all of it: **a doubtful event is dropped, never
repaired**. Clamping a 104% into 100% produces a number that looks right and
hides that two counters disagreed.
"""

from __future__ import annotations

import json
import unittest

from gshared_worker.job_runtime import RuntimeEvent, parse_event_line, parse_events
from gshared_worker.runtime_progress import (
    ProgressChannel,
    ProgressTracker,
    RuntimeProgressEvent,
)


def code_only(module) -> str:
    """The module's source with its docstrings removed.

    Searching a module for a forbidden name matches the comment explaining
    why the name is forbidden, which is how three of these tests first
    failed. This strips the prose and leaves the code.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(tree)


class Clock:
    """Fake monotonic seconds, moved only by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def line(name: str, **fields) -> str:
    return "GSHARED_EVENT=" + json.dumps({"event": name, **fields})


def started(total_steps=10, epochs=2) -> str:
    return line("training_started", totalSteps=total_steps, epochs=epochs)


def step(n, total=10, epoch=1, total_epochs=2, elapsed=None) -> str:
    fields = {"step": n, "totalSteps": total, "currentEpoch": epoch,
              "totalEpochs": total_epochs}
    if elapsed is not None:
        fields["elapsedSec"] = elapsed
    return line("step", **fields)


class Watcher:
    """A tracker with its channel, and everything it published."""

    def __init__(self, job_id="123", clock=None):
        self.channel = ProgressChannel()
        self.clock = clock or Clock()
        self.published: list[RuntimeProgressEvent] = []
        self.tracker = ProgressTracker(
            job_id, self.channel.next_generation(), self.channel,
            monotonic=self.clock)
        self._watch()

    def _watch(self):
        publish = self.channel.publish
        record = self.published.append

        def watched(event):
            record(event)
            publish(event)

        self.channel.publish = watched   # type: ignore[assignment]

    def feed(self, *raw: str) -> None:
        for text in raw:
            event = parse_event_line(text)
            if event is not None:
                self.tracker.observe(event)


class TestTheEventsAreReadLive(unittest.TestCase):
    def test_a_step_becomes_a_percentage(self):
        watcher = Watcher()

        watcher.feed(started(total_steps=200), step(85, total=200, elapsed=123))

        published = watcher.published[-1]
        self.assertEqual(published.progress_percent, 42.5)
        self.assertEqual(published.current_step, 85)
        self.assertEqual(published.total_steps, 200)
        self.assertEqual(published.elapsed_sec, 123)

    def test_training_started_publishes_nothing_on_its_own(self):
        """There is no step yet and no honest way to invent one.

        Sending zeroes would have the Backend store a currentStep of 0 out of
        a totalSteps of 0, which is not "0%" -- it is two numbers made up to
        fill a shape.
        """
        watcher = Watcher()

        watcher.feed(started())

        self.assertEqual(watcher.published, [])

    def test_a_step_before_training_started_is_not_published(self):
        # Out of order, or something else on stdout imitating the contract.
        watcher = Watcher()

        watcher.feed(step(1))

        self.assertEqual(watcher.published, [])

    def test_training_completed_finishes_at_a_hundred(self):
        watcher = Watcher()

        watcher.feed(started(total_steps=10), step(3, elapsed=5),
                     line("training_completed", totalSteps=10, runtimeSec=9.5))

        self.assertEqual(watcher.published[-1].progress_percent, 100.0)
        self.assertEqual(watcher.published[-1].current_step, 10)

    def test_a_failed_event_publishes_no_progress(self):
        """A failure is not a percentage.

        It belongs to the outcome, which task 9 turns into JOB_FAILED.
        Publishing it here would show a customer a run that looked fine.
        """
        watcher = Watcher()

        watcher.feed(started(), step(1, elapsed=1),
                     line("failed", errorCode="CUDA_OOM", message="no memory"))

        self.assertEqual(len(watcher.published), 1)

    def test_nothing_more_is_published_after_a_failure(self):
        watcher = Watcher()

        watcher.feed(started(), line("failed", errorCode="X", message="y"),
                     step(5, elapsed=5))

        self.assertEqual(watcher.published, [])

    def test_a_closed_tracker_ignores_a_late_line(self):
        watcher = Watcher()
        watcher.feed(started(), step(1, elapsed=1))

        watcher.tracker.close()
        watcher.feed(step(2, elapsed=2))

        self.assertEqual(len(watcher.published), 1)


class TestLinesThatAreNotEvents(unittest.TestCase):
    def test_ordinary_output_is_not_an_event(self):
        for noise in ("Loading checkpoint shards:  50%|#####     |",
                      "warn: bitsandbytes was compiled without GPU support",
                      "", "   "):
            with self.subTest(noise=noise):
                self.assertIsNone(parse_event_line(noise))

    def test_a_broken_event_is_skipped_and_the_rest_read(self):
        watcher = Watcher()

        watcher.feed(started(), "GSHARED_EVENT={broken", step(4, elapsed=4))

        self.assertEqual(len(watcher.published), 1)
        self.assertEqual(watcher.published[0].current_step, 4)

    def test_an_event_that_is_not_an_object_is_skipped(self):
        for text in ('GSHARED_EVENT=[1,2,3]', 'GSHARED_EVENT="step"',
                     'GSHARED_EVENT=null', 'GSHARED_EVENT={"event":""}',
                     'GSHARED_EVENT={"event":7}'):
            with self.subTest(text=text):
                self.assertIsNone(parse_event_line(text))


class TestOneParserForBothPaths(unittest.TestCase):
    """The same line must mean the same thing live and in the record.

    Two copies of the rules would agree on the day they were written.
    """

    def test_reading_a_whole_stdout_uses_the_line_parser(self):
        text = "\n".join(["noise", started(), step(1, elapsed=1), "more noise"])

        whole = parse_events(text)
        piecewise = [event for event in
                     (parse_event_line(one) for one in text.splitlines())
                     if event is not None]

        self.assertEqual([(e.name, e.payload) for e in whole],
                         [(e.name, e.payload) for e in piecewise])

    def test_the_streaming_path_and_the_offline_path_agree(self):
        text = "\n".join([started(), step(7, total=10, elapsed=12)])

        offline = parse_events(text)[-1]
        live = parse_event_line(step(7, total=10, elapsed=12))

        self.assertEqual(offline.progress_percent, live.progress_percent)
        self.assertEqual(offline.current_epoch, live.current_epoch)
        self.assertEqual(offline.elapsed_sec, live.elapsed_sec)


class TestTheNumbersAreChecked(unittest.TestCase):
    def test_a_step_past_the_total_is_dropped_not_clamped(self):
        watcher = Watcher()

        watcher.feed(started(total_steps=10), step(11, total=10, elapsed=1))

        self.assertEqual(watcher.published, [])

    def test_a_total_of_zero_is_not_divided_by(self):
        watcher = Watcher()

        watcher.feed(line("training_started", totalSteps=0),
                     line("step", step=1, totalSteps=0))

        self.assertEqual(watcher.published, [])

    def test_a_negative_step_is_dropped(self):
        watcher = Watcher()

        watcher.feed(started(), step(-1, elapsed=1))

        self.assertEqual(watcher.published, [])

    def test_a_step_that_is_not_a_number_is_dropped(self):
        watcher = Watcher()

        watcher.feed(started(), line("step", step="7", totalSteps=10))

        self.assertEqual(watcher.published, [])

    def test_a_boolean_is_not_a_step(self):
        # True == 1 in Python, which is exactly how it would slip through.
        watcher = Watcher()

        watcher.feed(started(), line("step", step=True, totalSteps=10))

        self.assertEqual(watcher.published, [])

    def test_every_percentage_is_finite_and_in_range(self):
        watcher = Watcher()

        watcher.feed(started(total_steps=7),
                     *[step(n, total=7, elapsed=n) for n in range(1, 8)])

        self.assertEqual(len(watcher.published), 7)
        for published in watcher.published:
            self.assertTrue(0.0 <= published.progress_percent <= 100.0)
            self.assertEqual(published.progress_percent,
                             published.progress_percent)   # not NaN
        self.assertEqual(watcher.published[-1].progress_percent, 100.0)

    def test_a_non_finite_elapsed_falls_back_to_our_own_clock(self):
        """The percentage survives a container with a broken clock.

        Dropping the whole update would cost the customer the number they
        actually look at, for the sake of a second nobody reads. What must
        never happen is publishing the infinity, and that is checked.
        """
        clock = Clock()
        watcher = Watcher(clock=clock)
        watcher.feed(started())
        clock.advance(8.0)

        # json.dumps would refuse to write these; a broken container would not.
        watcher.tracker.observe(RuntimeEvent("step", {
            "step": 1, "totalSteps": 10, "elapsedSec": float("inf")}))

        self.assertEqual(watcher.published[-1].elapsed_sec, 8)

    def test_a_negative_elapsed_falls_back_too(self):
        clock = Clock()
        watcher = Watcher(clock=clock)
        watcher.feed(started())
        clock.advance(3.5)

        watcher.feed(step(1, elapsed=-4))

        self.assertEqual(watcher.published[-1].elapsed_sec, 3)

    def test_every_published_elapsed_is_finite_and_not_negative(self):
        clock = Clock()
        watcher = Watcher(clock=clock)
        watcher.feed(started(total_steps=4))

        for n, reported in enumerate([float("nan"), -1, 5, float("inf")], 1):
            clock.advance(1.0)
            watcher.tracker.observe(RuntimeEvent("step", {
                "step": n, "totalSteps": 4, "elapsedSec": reported}))

        for published in watcher.published:
            self.assertGreaterEqual(published.elapsed_sec, 0)
            self.assertIsInstance(published.elapsed_sec, int)

    def test_elapsed_is_whole_seconds(self):
        watcher = Watcher()

        watcher.feed(started(), step(1, elapsed=12.9))

        # The Backend column is INT. Floored, so it never claims a second
        # that has not finished.
        self.assertEqual(watcher.published[-1].elapsed_sec, 12)
        self.assertIsInstance(watcher.published[-1].elapsed_sec, int)


class TestWhereTheEpochComesFrom(unittest.TestCase):
    """Never from arithmetic on the step count. See the note in train.py."""

    def test_the_container_says_which_epoch(self):
        watcher = Watcher()

        watcher.feed(started(), step(5, epoch=2, total_epochs=3, elapsed=5))

        self.assertEqual(watcher.published[-1].current_epoch, 2)
        self.assertEqual(watcher.published[-1].total_epochs, 3)

    def test_an_event_with_no_epoch_reports_none(self):
        watcher = Watcher()

        watcher.feed(line("training_started", totalSteps=10),
                     line("step", step=4, totalSteps=10, elapsedSec=4))

        self.assertIsNone(watcher.published[-1].current_epoch)

    def test_an_epoch_past_the_total_is_refused(self):
        watcher = Watcher()

        watcher.feed(started(), step(5, epoch=9, total_epochs=3, elapsed=5))

        self.assertIsNone(watcher.published[-1].current_epoch)

    def test_the_step_is_still_published_when_the_epoch_is_not(self):
        # A doubtful epoch is not a reason to lose the percentage.
        watcher = Watcher()

        watcher.feed(started(), step(5, epoch=9, total_epochs=3, elapsed=5))

        self.assertEqual(watcher.published[-1].progress_percent, 50.0)

    def test_nothing_in_the_worker_divides_steps_by_epochs(self):
        import ast
        import inspect

        from gshared_worker import runtime_progress

        tree = ast.parse(inspect.getsource(runtime_progress))
        # The only division here is step/total for the percentage. A second
        # one would be the guess this design exists to avoid.
        divisions = [node for node in ast.walk(tree)
                     if isinstance(node, ast.BinOp)
                     and isinstance(node.op, (ast.Div, ast.FloorDiv))]
        self.assertEqual(len(divisions), 1)
        self.assertNotIn("ceil", code_only(runtime_progress))


class TestProgressOnlyGoesForward(unittest.TestCase):
    def test_a_later_step_is_published(self):
        watcher = Watcher()

        watcher.feed(started(), step(1, elapsed=1), step(2, elapsed=2))

        self.assertEqual([p.current_step for p in watcher.published], [1, 2])

    def test_the_same_step_twice_is_published_once(self):
        watcher = Watcher()

        watcher.feed(started(), step(3, elapsed=3), step(3, elapsed=4))

        self.assertEqual([p.current_step for p in watcher.published], [3])

    def test_a_step_that_went_backwards_is_dropped(self):
        watcher = Watcher()

        watcher.feed(started(), step(10, elapsed=10), step(9, elapsed=11))

        self.assertEqual([p.current_step for p in watcher.published], [10])

    def test_the_percentage_never_falls(self):
        watcher = Watcher()

        watcher.feed(started(total_steps=10), step(5, elapsed=5),
                     step(4, elapsed=6), step(6, elapsed=7))

        percents = [p.progress_percent for p in watcher.published]
        self.assertEqual(percents, sorted(percents))

    def test_nothing_is_published_after_a_hundred_percent(self):
        watcher = Watcher()

        watcher.feed(started(total_steps=4), step(4, total=4, elapsed=8),
                     step(4, total=4, elapsed=9))

        self.assertEqual(len(watcher.published), 1)

    def test_an_elapsed_that_went_backwards_is_held_not_dropped(self):
        """The step behind it is real progress; a stalled second is cheaper.

        Dropping the update would lose a percentage the customer can see for
        the sake of a second nobody reads.
        """
        watcher = Watcher()

        watcher.feed(started(), step(1, elapsed=30), step(2, elapsed=12))

        self.assertEqual([p.current_step for p in watcher.published], [1, 2])
        self.assertEqual([p.elapsed_sec for p in watcher.published], [30, 30])

    def test_the_workers_own_clock_is_used_when_the_container_is_silent(self):
        clock = Clock()
        watcher = Watcher(clock=clock)

        watcher.feed(line("training_started", totalSteps=10))
        clock.advance(41.7)
        watcher.feed(line("step", step=1, totalSteps=10))

        self.assertEqual(watcher.published[-1].elapsed_sec, 41)

    def test_that_clock_is_monotonic_and_not_the_wall_clock(self):
        """Checked against the code, not the prose that explains it.

        The first version of this searched the whole module and matched the
        comment saying why ``datetime.now()`` is not used -- a test that
        failed for quoting the rule it was enforcing.
        """
        from gshared_worker import runtime_progress

        source = code_only(runtime_progress)
        self.assertIn("time.monotonic", source)
        for absent in ("datetime", "time.time(", "utcnow"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestTheChannelHoldsOne(unittest.TestCase):
    def test_ten_thousand_events_leave_one_value(self):
        """Progress is a state, not a log.

        A queue would deliver a history nobody wants -- and, while a
        connection is down, would grow for the length of the run.
        """
        channel = ProgressChannel()
        tracker = ProgressTracker("123", channel.next_generation(), channel)
        tracker.observe(parse_event_line(started(total_steps=10000)))

        for n in range(1, 10001):
            tracker.observe(parse_event_line(
                step(n, total=10000, elapsed=n // 10)))

        taken = channel.take()
        self.assertEqual(taken.current_step, 10000)
        self.assertIsNone(channel.take(), "the box should now be empty")

    def test_taking_twice_gives_nothing_the_second_time(self):
        channel = ProgressChannel()
        channel.publish(_a_progress(channel))

        self.assertIsNotNone(channel.take())
        self.assertIsNone(channel.take())

    def test_peeking_does_not_take(self):
        channel = ProgressChannel()
        channel.publish(_a_progress(channel))

        self.assertIsNotNone(channel.peek())
        self.assertIsNotNone(channel.take())

    def test_it_can_be_offered_again_once(self):
        channel = ProgressChannel()
        channel.publish(_a_progress(channel))
        channel.take()

        channel.resend_on_next_take()

        self.assertIsNotNone(channel.take())
        self.assertIsNone(channel.take())

    def test_offering_again_with_nothing_there_offers_nothing(self):
        channel = ProgressChannel()

        channel.resend_on_next_take()

        self.assertIsNone(channel.take())


class TestOneRunDoesNotSpeakForAnother(unittest.TestCase):
    """A container that has exited can still have a line in the pipe.

    The job id cannot tell the two apart -- a Job retried on this machine
    carries the same one -- so each run gets a number of its own.
    """

    def test_a_new_run_forgets_the_last_ones_progress(self):
        channel = ProgressChannel()
        channel.publish(_a_progress(channel))

        channel.next_generation()

        self.assertIsNone(channel.take())
        self.assertIsNone(channel.peek())

    def test_a_late_event_from_the_previous_run_is_refused(self):
        channel = ProgressChannel()
        old = ProgressTracker("123", channel.next_generation(), channel)
        channel.next_generation()          # a second run started

        old.observe(parse_event_line(started()))
        old.observe(parse_event_line(step(5, elapsed=5)))

        self.assertIsNone(channel.take())

    def test_the_current_run_publishes_normally(self):
        channel = ProgressChannel()
        channel.next_generation()
        current = ProgressTracker("123", channel.next_generation(), channel)

        current.observe(parse_event_line(started()))
        current.observe(parse_event_line(step(5, elapsed=5)))

        self.assertEqual(channel.take().current_step, 5)


def _a_progress(channel: ProgressChannel) -> RuntimeProgressEvent:
    return RuntimeProgressEvent(
        job_id="123", generation=channel.peek().generation if channel.peek()
        else channel.next_generation(),
        progress_percent=50.0, current_step=5, total_steps=10, elapsed_sec=7)


if __name__ == "__main__":
    unittest.main()
