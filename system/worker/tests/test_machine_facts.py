"""What this machine says about itself, and what it refuses to guess.

The rule this file exists to hold: a machine that cannot describe its GPU is
described as nothing at all. Filling in a plausible card would put a number the
scheduler believes in front of hardware nobody looked at.
"""

from __future__ import annotations

import os
import unittest

from gshared_worker.command_runner import CommandResult
from gshared_worker.errors import CommandError
from gshared_worker.machine_facts import MachineFacts, describe_machine

ONE_GPU = "NVIDIA GeForce RTX 4070 Laptop GPU, 596.36, 8188\n"


class Runner:
    """Answers nvidia-smi as a test says, and records what it was asked."""

    def __init__(self, stdout=ONE_GPU, exit_code=0, raises=None):
        self._stdout = stdout
        self._exit_code = exit_code
        self._raises = raises
        self.asked: list[list[str]] = []

    async def run(self, args, *, timeout, max_output_bytes):
        self.asked.append(args)
        if self._raises is not None:
            raise self._raises
        return CommandResult(self._exit_code, self._stdout, "")


class DescribingThisMachineTest(unittest.TestCase):
    def test_the_gpu_comes_from_the_drivers_own_tool(self):
        facts = describe_machine(Runner())

        self.assertEqual(facts.gpu_model, "NVIDIA GeForce RTX 4070 Laptop GPU")
        self.assertEqual(facts.driver_version, "596.36")
        self.assertEqual(facts.vram_mb, 8188)

    def test_it_asks_for_exactly_the_three_fields_it_needs(self):
        runner = Runner()

        describe_machine(runner)

        asked = " ".join(runner.asked[0])
        self.assertIn("--query-gpu=name,driver_version,memory.total", asked)
        self.assertIn("nounits", asked)

    def test_nothing_it_runs_changes_the_machine(self):
        runner = Runner()

        describe_machine(runner)

        self.assertEqual(runner.asked[0][0], "nvidia-smi")
        self.assertEqual(len(runner.asked), 1)

    def test_the_device_name_and_os_are_this_machines_own(self):
        import platform

        facts = describe_machine(Runner())

        self.assertEqual(facts.device_name, platform.node())
        self.assertTrue(facts.os_version)

    def test_the_first_card_is_enough_when_there_are_several(self):
        runner = Runner(stdout="Card A, 500.00, 1024\nCard B, 500.00, 2048\n")

        self.assertEqual(describe_machine(runner).gpu_model, "Card A")


class WhatIsNeverInventedTest(unittest.TestCase):
    def test_no_driver_tool_means_no_facts(self):
        described = describe_machine(
            Runner(raises=CommandError("COMMAND_NOT_FOUND", "nvidia-smi was not found")))

        self.assertIsNone(described)

    def test_a_tool_that_failed_means_no_facts(self):
        self.assertIsNone(describe_machine(Runner(exit_code=9, stdout="")))

    def test_a_card_that_named_no_memory_means_no_facts(self):
        # vramMb is what the Backend schedules against. A card whose memory
        # nvidia-smi would not state cannot be offered.
        self.assertIsNone(describe_machine(Runner(stdout="Card A, 500.00\n")))

    def test_no_gpu_at_all_means_no_facts(self):
        self.assertIsNone(describe_machine(Runner(stdout="")))

    def test_there_is_no_default_card_anywhere_in_the_module(self):
        import inspect

        import gshared_worker.machine_facts as module

        source = inspect.getsource(module)

        for named_card in ("RTX", "GeForce", "Radeon", "8192", "unknown"):
            self.assertNotIn(named_card, source)
        # Built in one place, from what was read back. Never from constants.
        self.assertEqual(source.count("MachineFacts("), 1)


@unittest.skipUnless(os.name == "nt", "reads this Windows machine")
class OnThisRealMachineTest(unittest.TestCase):
    """No fake runner below this line."""

    def test_it_describes_this_pc_or_says_nothing(self):
        facts = describe_machine()

        if facts is None:
            self.skipTest("no NVIDIA driver on this machine")
        self.assertIsInstance(facts, MachineFacts)
        self.assertTrue(facts.device_name)
        self.assertTrue(facts.gpu_model)
        self.assertTrue(facts.driver_version)
        self.assertGreater(facts.vram_mb, 0)
        self.assertIn("Windows", facts.os_version)


if __name__ == "__main__":
    unittest.main()
