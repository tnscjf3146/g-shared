"""The numbers the Worker cannot work out for itself.

A step event used to carry ``epoch`` as the Trainer's fraction -- 0.21, 1.5 --
and nothing else about time. The Backend's progress row wants a whole epoch
number and a whole number of seconds, so somebody has to turn one into the
other.

Doing it on the Worker would mean dividing the step count by a guess at how
many steps an epoch holds. That guess is wrong whenever gradient accumulation
or a short final batch changes the count, and it is wrong *silently* -- the
number still looks like an epoch. So it is done here, where ``args`` and
``state`` are in hand, and the Worker reports what it was told or reports
nothing.

**No training behaviour is touched by any of this.** The optimiser, the LoRA
configuration, the quantisation, the batch and epoch semantics, the learning
rate and the schedule are all exactly as they were; the last test in this file
is what pins that down.
"""

from __future__ import annotations

import json
import unittest

from qlora_sft.events import EVENT_MARKER, EventWriter
from qlora_sft.train import ProgressEvents, _epoch_numbers


def code_of(thing) -> str:
    """The source of a class, with its comments and docstrings gone.

    ``ast.unparse`` drops comments on its own; the docstrings are removed
    here. What is left is only what runs, which is what these tests mean to
    search.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(thing)))
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


class Args:
    def __init__(self, logging_steps=1, num_train_epochs=3):
        self.logging_steps = logging_steps
        self.num_train_epochs = num_train_epochs


class State:
    def __init__(self, max_steps=6, epoch=0.0, global_step=0):
        self.global_step = global_step
        self.epoch = epoch
        self.max_steps = max_steps


class Recorder:
    def __init__(self):
        self.lines = []

    def writer(self):
        return EventWriter(self.lines.append)

    def events(self):
        return [json.loads(line[len(EVENT_MARKER):]) for line in self.lines]


def one_step(step, epoch, *, epochs=3, max_steps=6):
    recorder = Recorder()
    progress = ProgressEvents(recorder.writer(), total_steps=max_steps)
    args = Args(num_train_epochs=epochs)
    state = State(max_steps=max_steps, epoch=epoch, global_step=step)

    progress.on_train_begin(args, state, None)
    progress.on_step_end(args, state, None)
    progress.on_log(args, state, None, logs={"loss": 1.5})
    return recorder.events()[-1]


class TestTheStepEventCarriesTheEpoch(unittest.TestCase):
    def test_the_first_pass_over_the_data_is_epoch_one(self):
        # Not zero. A customer counting epochs starts at one.
        self.assertEqual(one_step(1, epoch=0.16)["currentEpoch"], 1)

    def test_partway_through_the_second_pass_is_epoch_two(self):
        self.assertEqual(one_step(3, epoch=1.5)["currentEpoch"], 2)

    def test_the_total_comes_from_the_trainer_arguments(self):
        self.assertEqual(one_step(1, epoch=0.1, epochs=3)["totalEpochs"], 3)

    def test_the_very_end_does_not_name_an_epoch_that_does_not_exist(self):
        """``state.epoch`` reaches the total exactly on the last step.

        Read naively that is "epoch 4 of 3". The last one is held instead.
        """
        last = one_step(6, epoch=3.0, epochs=3)

        self.assertEqual(last["currentEpoch"], 3)
        self.assertEqual(last["totalEpochs"], 3)

    def test_the_fractional_epoch_is_still_there_unchanged(self):
        # The existing field. Nothing that read it before should notice this.
        self.assertEqual(one_step(3, epoch=1.5)["epoch"], 1.5)


class TestWhenTheTrainerCannotSay(unittest.TestCase):
    """A fabricated epoch is worse than an absent one."""

    def test_a_run_with_no_epoch_total_reports_neither(self):
        self.assertEqual(_epoch_numbers(Args(num_train_epochs=None), State()),
                         (None, None))

    def test_a_zero_epoch_total_reports_neither(self):
        self.assertEqual(_epoch_numbers(Args(num_train_epochs=0), State()),
                         (None, None))

    def test_a_boolean_is_not_an_epoch_count(self):
        self.assertEqual(_epoch_numbers(Args(num_train_epochs=True), State()),
                         (None, None))

    def test_an_arguments_object_without_the_field_reports_neither(self):
        class Bare:
            logging_steps = 1

        self.assertEqual(_epoch_numbers(Bare(), State()), (None, None))

    def test_a_negative_position_reports_neither(self):
        self.assertEqual(
            _epoch_numbers(Args(num_train_epochs=3), State(epoch=-1.0)),
            (None, None))

    def test_those_fields_are_left_out_of_the_line_entirely(self):
        recorder = Recorder()
        recorder.writer().step(step=1, total_steps=4, epoch=0.25)

        event = recorder.events()[-1]
        for absent in ("currentEpoch", "totalEpochs", "elapsedSec"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, event)

    def test_the_loss_is_still_reported_as_null_when_there_is_none(self):
        # Unlike the three above: a null loss is a measurement that came out
        # empty, and readers already depend on the key being there.
        recorder = Recorder()
        recorder.writer().step(step=1, total_steps=4, epoch=0.25)

        self.assertIn("loss", recorder.events()[-1])
        self.assertIsNone(recorder.events()[-1]["loss"])


class TestTheStepEventCarriesTheElapsedTime(unittest.TestCase):
    def test_it_is_measured_and_reported(self):
        event = one_step(2, epoch=1.0)

        self.assertIn("elapsedSec", event)
        self.assertGreaterEqual(event["elapsedSec"], 0.0)

    def test_it_grows(self):
        recorder = Recorder()
        progress = ProgressEvents(recorder.writer(), total_steps=3)
        args = Args()
        progress.on_train_begin(args, State(), None)

        for step in (1, 2, 3):
            state = State(max_steps=3, epoch=step / 3, global_step=step)
            progress.on_log(args, state, None, logs={"loss": 1.0})

        elapsed = [event["elapsedSec"] for event in recorder.events()]
        self.assertEqual(elapsed, sorted(elapsed))

    def test_the_clock_is_monotonic(self):
        """A run measured with the wall clock changes length when the host
        syncs its time, and can come out negative.

        Checked against the code with its prose removed. Searching the whole
        source matched the comment saying why ``time.time()`` is not used --
        a test that failed for quoting the rule it enforces.
        """
        from qlora_sft import train

        code = code_of(train.ProgressEvents)

        self.assertIn("time.monotonic", code)
        self.assertNotIn("time.time()", code)
        self.assertNotIn("datetime", code)


class TestTrainingItselfIsUntouched(unittest.TestCase):
    """The one rule this change was not allowed to break.

    Progress is observability. If any of these moved, a customer's adapter
    would come out different from the one the same configuration produced
    yesterday, and the reason would be a progress bar.
    """

    def test_the_lora_and_quantisation_constants_are_what_they_were(self):
        from qlora_sft import train

        self.assertEqual(train.LORA_R, 16)
        self.assertEqual(train.LORA_ALPHA, 32)
        self.assertEqual(train.LORA_DROPOUT, 0.05)
        self.assertEqual(train.QUANT_TYPE, "nf4")
        self.assertTrue(train.DOUBLE_QUANT)

    def test_the_trainer_arguments_are_unchanged(self):
        import inspect

        from qlora_sft import train

        source = inspect.getsource(train._train)
        for expected in ('optim="paged_adamw_8bit"',
                         "num_train_epochs=config.epochs",
                         "per_device_train_batch_size=config.batch_size",
                         "learning_rate=config.learning_rate",
                         "gradient_accumulation_steps="
                         "config.gradient_accumulation_steps",
                         "seed=config.seed",
                         'save_strategy="no"'):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)

    def test_the_callback_reports_and_does_not_steer(self):
        """It may read ``state`` and ``args``. It may not write to them.

        ``control`` is how a callback changes what the Trainer does next --
        stopping it, forcing a save. Nothing here touches it.
        """
        import ast
        import inspect
        import textwrap

        from qlora_sft import train

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(train.ProgressEvents)))
        written = [target.attr for node in ast.walk(tree)
                   if isinstance(node, ast.Assign)
                   for target in node.targets
                   if isinstance(target, ast.Attribute)]

        # Only its own bookkeeping -- nothing on args, state or control.
        self.assertTrue(written, "the callback assigns nothing at all?")
        self.assertTrue(all(name.startswith("_") for name in written), written)
        code = code_of(train.ProgressEvents)
        for absent in ("should_training_stop", "should_save",
                       "should_evaluate", "control."):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, code)


if __name__ == "__main__":
    unittest.main()
