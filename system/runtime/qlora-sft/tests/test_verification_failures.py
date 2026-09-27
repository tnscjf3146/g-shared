"""Two kinds of failure, kept apart.

* The **description** of a result cannot be produced -- the snapshot, the
  comparison or the heatmap throws. The job is not failed for that; the
  metrics say ``valid: null`` with an error code.
* The **result itself** is broken -- a file is missing, the adapter does not
  load back. That is still ``OUTPUT_VALIDATION_FAILED``, exactly as before.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
import unittest

from qlora_sft import train
from qlora_sft.failures import ExitCode
from qlora_sft.verification import (VERIFICATION_METADATA_UNAVAILABLE, try_snapshot,
                                    try_verification_report, unavailable_report)


class Exploding:
    def named_parameters(self):
        raise RuntimeError("cannot walk this model")


class TestTheDegradedState(unittest.TestCase):
    def test_its_shape(self):
        self.assertEqual(unavailable_report(), {
            "schemaVersion": 1, "valid": None,
            "errorCode": "VERIFICATION_METADATA_UNAVAILABLE"})

    def test_it_serialises_with_null(self):
        text = json.dumps({"verification": unavailable_report()}, allow_nan=False)

        self.assertIn('"valid": null', text)
        self.assertIn(VERIFICATION_METADATA_UNAVAILABLE, text)

    def test_a_snapshot_that_throws_gives_none_not_an_exception(self):
        self.assertIsNone(try_snapshot(Exploding()))

    def test_no_snapshot_means_unavailable(self):
        self.assertEqual(try_verification_report(None, Exploding()), unavailable_report())

    def test_a_comparison_that_throws_means_unavailable(self):
        self.assertEqual(try_verification_report({"x": object()}, Exploding()),
                         unavailable_report())

    def test_unavailable_is_null_not_false(self):
        # false would read as "the adapter was checked and is bad".
        self.assertIsNone(unavailable_report()["valid"])


class TestTheResultItselfIsStillChecked(unittest.TestCase):
    def test_output_validation_still_runs_after_training(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(train.run)))
        called = [n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]

        self.assertIn("_check_output", called)
        self.assertIn("_save", called)

    def test_verification_is_not_wrapped_around_output_validation(self):
        # _check_output is called at the top level of run(), not inside a try.
        tree = ast.parse(textwrap.dedent(inspect.getsource(train.run)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                inner = [n.func.id for n in ast.walk(node)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
                self.assertNotIn("_check_output", inner)

    def test_the_output_failure_code_is_unchanged(self):
        self.assertEqual(int(ExitCode.OUTPUT_VALIDATION_FAILED), 15)
        self.assertEqual(int(ExitCode.OK), 0)

    def test_no_new_exit_code_for_verification(self):
        names = {code.name for code in ExitCode}

        self.assertFalse({n for n in names if "VERIF" in n})


if __name__ == "__main__":
    unittest.main()
