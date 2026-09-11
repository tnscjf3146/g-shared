"""One progress event per step, carrying that step's own loss.

The first GPU smoke run of this runtime published::

    {"event":"step","step":1,"loss":null}
    {"event":"step","step":2,"loss":3.291152}

3.291152 was step 1's loss. The Trainer runs ``on_step_end`` before it logs,
so a callback that emitted on ``on_step_end`` and used the last loss it had
seen attached every loss to the following step. Nothing failed -- the numbers
were plausible and the run exited 0 -- which is why it is pinned here.

These tests drive the hooks in the order ``transformers.Trainer`` calls them,
without transformers being installed.
"""

from __future__ import annotations

import json
import unittest

from qlora_sft.events import EVENT_MARKER, EventWriter
from qlora_sft.train import ProgressEvents


class Args:
    def __init__(self, logging_steps=1):
        self.logging_steps = logging_steps


class State:
    def __init__(self, max_steps=3):
        self.global_step = 0
        self.epoch = 0.0
        self.max_steps = max_steps


class Recorder:
    def __init__(self):
        self.lines = []

    def writer(self):
        return EventWriter(self.lines.append)

    def steps(self):
        return [json.loads(line[len(EVENT_MARKER):]) for line in self.lines]


def train(losses, logging_steps=1):
    """Drive the callback the way the Trainer does, for len(losses) steps."""
    recorder = Recorder()
    progress = ProgressEvents(recorder.writer(), total_steps=len(losses))
    args, state = Args(logging_steps), State(max_steps=len(losses))

    for number, loss in enumerate(losses, start=1):
        state.global_step = number
        state.epoch = number / len(losses)
        progress.on_step_end(args, state, None)          # first ...
        if logging_steps == 1:
            progress.on_log(args, state, None, logs={     # ... then this
                "loss": loss, "epoch": state.epoch})
    return recorder.steps()


class TestEachStepCarriesItsOwnLoss(unittest.TestCase):
    def test_the_losses_are_not_shifted_by_one(self):
        events = train([3.291, 2.889, 2.877])

        self.assertEqual([event["loss"] for event in events],
                         [3.291, 2.889, 2.877])

    def test_the_first_step_has_a_loss(self):
        # It was null before. A reader plotting progress got a gap.
        self.assertIsNotNone(train([3.291, 2.889])[0]["loss"])

    def test_one_event_per_step(self):
        events = train([1.0, 2.0, 3.0])

        self.assertEqual([event["step"] for event in events], [1, 2, 3])

    def test_a_step_is_never_published_twice(self):
        steps = [event["step"] for event in train([1.0, 2.0, 3.0])]

        self.assertEqual(len(steps), len(set(steps)))


class TestProgressSurvivesLoggingChanges(unittest.TestCase):
    """``logging_steps`` is a training argument, not a progress contract."""

    def test_progress_still_arrives_when_logging_is_not_per_step(self):
        events = train([1.0, 2.0, 3.0], logging_steps=10)

        self.assertEqual([event["step"] for event in events], [1, 2, 3])

    def test_those_events_simply_have_no_loss(self):
        self.assertTrue(
            all(event["loss"] is None
                for event in train([1.0, 2.0], logging_steps=10)))


class TestTheDenominator(unittest.TestCase):
    def test_the_total_comes_from_the_trainer(self):
        # progressPercent = currentStep / totalSteps. The Trainer's own
        # max_steps is the number that cannot drift from what it will do.
        self.assertTrue(
            all(event["totalSteps"] == 3 for event in train([1.0, 2.0, 3.0])))

    def test_our_count_is_used_when_the_trainer_has_none(self):
        recorder = Recorder()
        progress = ProgressEvents(recorder.writer(), total_steps=42)
        state = State(max_steps=0)
        state.global_step = 1

        progress.on_log(Args(), state, None, logs={"loss": 1.0})

        self.assertEqual(recorder.steps()[0]["totalSteps"], 42)


class TestUnknownHooksAreHarmless(unittest.TestCase):
    def test_a_hook_this_class_does_not_implement_is_a_no_op(self):
        # The Trainer calls a dozen of these and does not check the type.
        progress = ProgressEvents(Recorder().writer(), total_steps=1)

        progress.on_train_begin(Args(), State(), None)
        progress.on_evaluate(Args(), State(), None)

    def test_a_non_hook_attribute_still_raises(self):
        progress = ProgressEvents(Recorder().writer(), total_steps=1)

        with self.assertRaises(AttributeError):
            progress.not_a_hook


if __name__ == "__main__":
    unittest.main()
