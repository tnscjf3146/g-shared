"""``lossHistory``: one point per optimizer step, in order, at most 200.

No torch here. The Trainer's log history is a list of dicts, and that is all
this needs.
"""

from __future__ import annotations

import json
import math
import unittest
from types import SimpleNamespace

from qlora_sft.train import _loss_curve
from qlora_sft.verification import MAX_LOSS_POINTS, downsample, loss_history


def logs(*pairs):
    return [{"loss": loss, "step": step, "epoch": 0.1, "learning_rate": 2e-4}
            for step, loss in pairs]


class TestOrderAndDuplicates(unittest.TestCase):
    def test_steps_come_out_ascending(self):
        history = loss_history(logs((3, 2.0), (1, 3.0), (2, 2.5)))

        self.assertEqual([p["step"] for p in history], [1, 2, 3])

    def test_a_repeated_step_keeps_its_last_value(self):
        history = loss_history(logs((1, 3.0), (2, 2.5), (2, 2.4)))

        self.assertEqual(history, [{"step": 1, "loss": 3.0}, {"step": 2, "loss": 2.4}])

    def test_only_step_and_loss_are_kept(self):
        (point,) = loss_history(logs((1, 3.0)))

        self.assertEqual(set(point), {"step", "loss"})

    def test_the_summary_entry_is_not_a_point(self):
        entries = logs((1, 3.0), (2, 2.5)) + [
            {"train_loss": 2.75, "train_runtime": 5.0, "step": 2}]

        self.assertEqual(len(loss_history(entries)), 2)


class TestEdges(unittest.TestCase):
    def test_no_history(self):
        self.assertEqual(loss_history([]), [])
        self.assertEqual(loss_history(None), [])

    def test_one_point(self):
        self.assertEqual(loss_history(logs((1, 3.2144449))), [{"step": 1, "loss": 3.214445}])

    def test_non_finite_losses_are_left_out_so_the_file_stays_json(self):
        history = loss_history(logs((1, 3.0), (2, math.nan), (3, math.inf), (4, 2.0)))

        self.assertEqual([p["step"] for p in history], [1, 4])
        json.dumps(history, allow_nan=False)

    def test_malformed_entries_are_skipped(self):
        entries = [None, "x", {"loss": 1.0}, {"step": "3", "loss": 1.0},
                   {"step": True, "loss": 1.0}, {"step": -1, "loss": 1.0},
                   {"step": 5, "loss": "1.0"}, {"step": 6, "loss": 1.5}]

        self.assertEqual(loss_history(entries), [{"step": 6, "loss": 1.5}])


class TestTheLimit(unittest.TestCase):
    def test_up_to_the_limit_nothing_is_dropped(self):
        for count in (2, 199, MAX_LOSS_POINTS):
            with self.subTest(count=count):
                history = loss_history(logs(*[(s, 1.0 / s) for s in range(1, count + 1)]))
                self.assertEqual(len(history), count)

    def test_over_the_limit_it_is_sampled_evenly_with_both_ends(self):
        history = loss_history(logs(*[(s, 10.0 / s) for s in range(1, 1001)]))
        steps = [p["step"] for p in history]

        self.assertEqual(len(history), MAX_LOSS_POINTS)
        self.assertEqual((steps[0], steps[-1]), (1, 1000))
        self.assertEqual(steps, sorted(set(steps)))
        gaps = {b - a for a, b in zip(steps, steps[1:])}
        self.assertLessEqual(max(gaps) - min(gaps), 1)

    def test_the_last_loss_survives_sampling(self):
        history = loss_history(logs(*[(s, 5.0 - s / 1000) for s in range(1, 5001)]))

        self.assertEqual(history[-1], {"step": 5000, "loss": 0.0})

    def test_downsample_refuses_a_limit_that_cannot_hold_both_ends(self):
        with self.assertRaises(ValueError):
            downsample(list(range(10)), 1)


class TestInTheMetrics(unittest.TestCase):
    def trainer(self, history):
        return SimpleNamespace(state=SimpleNamespace(log_history=history))

    def test_final_loss_is_the_last_logged_step(self):
        curve = _loss_curve(self.trainer(logs((1, 3.0), (2, 2.0), (3, 1.5))))

        self.assertEqual(curve["finalLoss"], 1.5)
        self.assertEqual(curve["lossHistory"][-1], {"step": 3, "loss": 1.5})

    def test_an_unreadable_history_does_not_fail_the_run(self):
        class Broken:
            @property
            def state(self):
                raise RuntimeError("no state")

        self.assertEqual(_loss_curve(Broken()), {"lossHistory": []})

    def test_the_existing_metrics_keys_are_still_returned(self):
        import inspect

        from qlora_sft import train

        source = inspect.getsource(train._train)
        for key in ("trainLoss", "trainingRuntimeSec", "stepsPerSecond",
                    "samplesPerSecond", "peakVramMb", "peakVramReservedMb"):
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', source)
        self.assertIn("float(result.training_loss)", source)


if __name__ == "__main__":
    unittest.main()
