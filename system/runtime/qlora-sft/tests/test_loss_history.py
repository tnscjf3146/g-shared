"""The per-step losses are kept, not only streamed.

``ProgressEvents`` has always seen every step's loss -- it publishes one
progress event per step and the number is right (see ``test_progress``). It
then dropped it. ``training_metrics.json`` carried a single ``trainLoss``,
the Trainer's mean over the whole run, and the job detail screen drew an
empty chart saying "Step별 Loss 기록이 제공되지 않습니다".

So the callback now also accumulates, and the run writes the curve it already
knew. These tests drive the hooks the way ``transformers.Trainer`` does,
without transformers installed.
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


def run(losses, logging_steps=1):
    """Returns the callback after driving it for ``len(losses)`` steps."""
    progress = ProgressEvents(EventWriter(lambda _line: None),
                              total_steps=len(losses))
    args, state = Args(logging_steps), State(max_steps=len(losses))
    for number, loss in enumerate(losses, start=1):
        state.global_step = number
        state.epoch = number / len(losses)
        progress.on_step_end(args, state, None)
        if logging_steps == 1:
            progress.on_log(args, state, None,
                            logs={"loss": loss, "epoch": state.epoch})
    return progress


class LossHistory(unittest.TestCase):

    def test_one_point_per_step_in_order(self):
        history = run([3.291, 2.889, 2.877]).loss_history()

        self.assertEqual(history, [{"step": 1, "loss": 3.291},
                                   {"step": 2, "loss": 2.889},
                                   {"step": 3, "loss": 2.877}])

    def test_the_last_point_is_the_final_loss(self):
        """What the screen labels "Final Loss". The Trainer's own
        ``training_loss`` is the mean over the run, not this."""
        self.assertEqual(run([3.0, 2.0, 1.5]).final_loss(), 1.5)

    def test_final_loss_without_any_step(self):
        """A run that logged nothing reports no final loss rather than 0.0 --
        a zero here would read as a perfectly trained model."""
        self.assertIsNone(run([]).final_loss())

    def test_no_history_without_any_step(self):
        self.assertEqual(run([]).loss_history(), [])

    def test_a_step_with_no_loss_is_not_recorded(self):
        """``on_step_end`` publishes a progress event with a null loss when
        logging will not cover the step. A null belongs in the event stream,
        not in a chart."""
        progress = ProgressEvents(EventWriter(lambda _line: None),
                                  total_steps=2)
        args, state = Args(logging_steps=5), State(max_steps=2)
        for number in (1, 2):
            state.global_step = number
            progress.on_step_end(args, state, None)

        self.assertEqual(progress.loss_history(), [])

    def test_a_step_is_not_recorded_twice(self):
        """Both hooks fire for the same step; only the first one that carries
        a loss counts. The event stream already guards this -- the history
        has to agree with it or the chart has more points than there were
        steps."""
        progress = ProgressEvents(EventWriter(lambda _line: None),
                                  total_steps=1)
        args, state = Args(), State(max_steps=1)
        state.global_step = 1

        progress.on_log(args, state, None, logs={"loss": 2.5})
        progress.on_log(args, state, None, logs={"loss": 9.9})

        self.assertEqual(progress.loss_history(), [{"step": 1, "loss": 2.5}])

    def test_history_does_not_disturb_the_event_stream(self):
        """The events are the contract the worker reads; accumulating must
        not change them."""
        lines = []
        progress = ProgressEvents(EventWriter(lines.append), total_steps=1)
        args, state = Args(), State(max_steps=1)
        state.global_step = 1
        progress.on_log(args, state, None, logs={"loss": 2.5})

        published = json.loads(lines[0][len(EVENT_MARKER):])
        self.assertEqual(published["step"], 1)
        self.assertEqual(published["loss"], 2.5)


if __name__ == "__main__":
    unittest.main()
