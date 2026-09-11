"""The stdout contract task 8 will read.

JOB_PROGRESS is not implemented here and must not be. What these tests pin
down is the thing that makes it cheap to implement later and impossible to
implement twice: every event is one marked line of JSON, and progress is a
step count rather than an epoch fraction.
"""

from __future__ import annotations

import json
import unittest

from qlora_sft.events import (EVENT_MARKER, EVENT_SCHEMA_VERSION, EventWriter,
                              format_event)


def payload(line: str) -> dict:
    assert line.startswith(EVENT_MARKER), line
    return json.loads(line[len(EVENT_MARKER):])


class Captured(EventWriter):
    def __init__(self):
        self.lines = []
        super().__init__(self.lines.append)

    def payloads(self):
        return [payload(line) for line in self.lines]


class TestTheLineFormat(unittest.TestCase):
    def test_an_event_is_one_line(self):
        self.assertNotIn("\n", format_event("step", step=1))

    def test_an_event_is_marked(self):
        # stdout is shared with a training stack that writes banners and
        # warnings. A reader that took every line as JSON would drown.
        self.assertTrue(format_event("step").startswith(EVENT_MARKER))

    def test_the_body_is_json(self):
        self.assertEqual(payload(format_event("step", step=4))["step"], 4)

    def test_every_event_names_itself(self):
        self.assertEqual(payload(format_event("training_started"))["event"],
                         "training_started")

    def test_every_event_carries_the_schema_version(self):
        # A reader that does not recognise the version should refuse the
        # stream rather than guess at its shape.
        self.assertEqual(payload(format_event("step"))["version"],
                         EVENT_SCHEMA_VERSION)

    def test_korean_text_is_not_escaped_into_noise(self):
        self.assertIn("실패", format_event("failed", message="학습 실패"))


class TestNumbersThatAreNotNumbers(unittest.TestCase):
    def test_a_nan_loss_becomes_null_rather_than_breaking_the_line(self):
        # A diverging run is exactly when a reader most wants the event, and
        # bare NaN is not JSON -- the one line that mattered would fail to
        # parse on the other side.
        body = payload(format_event("step", loss=float("nan")))

        self.assertIsNone(body["loss"])

    def test_an_infinite_loss_becomes_null(self):
        self.assertIsNone(payload(format_event("step", loss=float("inf")))["loss"])

    def test_an_ordinary_loss_survives(self):
        self.assertAlmostEqual(
            payload(format_event("step", loss=1.2345))["loss"], 1.2345)


class TestTheEventsThemselves(unittest.TestCase):
    def test_training_started_says_how_many_steps_there_will_be(self):
        events = Captured()
        events.training_started(total_steps=100)

        self.assertEqual(events.payloads()[0]["totalSteps"], 100)

    def test_a_step_event_carries_step_and_total(self):
        # progressPercent = currentStep / totalSteps. Both halves are here.
        events = Captured()
        events.step(step=7, total_steps=100, epoch=0.21)

        body = events.payloads()[0]
        self.assertEqual((body["step"], body["totalSteps"]), (7, 100))

    def test_a_step_event_also_carries_the_epoch(self):
        events = Captured()
        events.step(step=7, total_steps=100, epoch=0.21)

        self.assertAlmostEqual(events.payloads()[0]["epoch"], 0.21)

    def test_progress_is_never_only_an_epoch(self):
        # An epoch count cannot answer "how far along is this?" when one epoch
        # is four hundred steps.
        events = Captured()
        events.step(step=1, total_steps=400, epoch=0.0025)

        self.assertIn("step", events.payloads()[0])
        self.assertIn("totalSteps", events.payloads()[0])

    def test_training_completed_carries_the_runtime(self):
        events = Captured()
        events.training_completed(runtime_sec=123.4)

        self.assertAlmostEqual(events.payloads()[0]["runtimeSec"], 123.4)

    def test_a_failure_carries_a_stable_code(self):
        events = Captured()
        events.failed(error_code="CUDA_OOM", message="out of memory")

        self.assertEqual(events.payloads()[0]["errorCode"], "CUDA_OOM")

    def test_events_arrive_in_order(self):
        events = Captured()
        events.training_started(total_steps=2)
        events.step(step=1, total_steps=2, epoch=0.5)
        events.step(step=2, total_steps=2, epoch=1.0)
        events.training_completed(runtime_sec=1.0)

        self.assertEqual([body["event"] for body in events.payloads()],
                         ["training_started", "step", "step",
                          "training_completed"])


class TestNothingIsSentAnywhere(unittest.TestCase):
    """Task 8 owns the WebSocket. This module owns a print statement."""

    def source(self) -> str:
        # Docstrings stripped first. This module's own prose explains that
        # JOB_PROGRESS is somebody else's task, and a grep cannot tell the
        # explanation from the thing being explained.
        from qlora_sft import events

        from tests.support import code_only

        return code_only(events)

    def test_no_websocket(self):
        for absent in ("websocket", "WebSocket", "JOB_PROGRESS"):
            self.assertNotIn(absent, self.source())

    def test_no_http(self):
        for absent in ("requests", "urllib", "httpx", "http.client"):
            self.assertNotIn(absent, self.source())


if __name__ == "__main__":
    unittest.main()
