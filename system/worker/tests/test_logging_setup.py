"""Everything diagnostic goes to one file beside the Worker, and nowhere else."""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker import logging_setup
from gshared_worker.logging_setup import (
    BUILD,
    DEBUG_LOG_NAME,
    LOGGER_NAME,
    build_stamp,
    stamp_the_build,
    start_debug_log,
    stop_debug_log,
)


class DebugLogTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._clean_up)

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    def written(self) -> str:
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class TheLogSitsBesideTheWorkerTest(DebugLogTestCase):
    def test_it_is_named_so_a_supplier_can_be_asked_to_send_it(self):
        path = start_debug_log(self.directory)

        self.assertEqual(path, self.directory / DEBUG_LOG_NAME)
        self.assertTrue(path.is_file())

    def test_what_a_component_logs_reaches_it(self):
        start_debug_log(self.directory)

        logging.getLogger(LOGGER_NAME).info("the first step began")

        self.assertIn("the first step began", self.written())

    def test_every_line_says_when_and_how_serious(self):
        start_debug_log(self.directory)

        logging.getLogger(LOGGER_NAME).info("something happened")

        self.assertIn("INFO", self.written())
        self.assertIn(LOGGER_NAME, self.written())


class TheTwoStreamsStaySeparateTest(DebugLogTestCase):
    def test_diagnostics_do_not_reach_the_supplier_screen(self):
        # The supplier's whole interface is what the program prints. A debug
        # line escaping to the root logger would be printed over the few lines
        # that matter to them.
        start_debug_log(self.directory)

        self.assertFalse(logging.getLogger(LOGGER_NAME).propagate)


class TheLogSaysWhichBuildWroteItTest(DebugLogTestCase):
    def test_the_build_is_stamped_at_the_top(self):
        # Without it a report cannot be told from an older copy of the
        # executable that was never replaced.
        start_debug_log(self.directory)

        stamp_the_build()

        self.assertIn(BUILD, self.written())

    def test_the_stamp_is_a_real_time_not_a_placeholder(self):
        self.assertTrue(build_stamp().strip())
        self.assertNotEqual(build_stamp(), "unrecorded")


class StartingTheLogTwiceTest(DebugLogTestCase):
    def test_one_line_is_written_once(self):
        # run() may be called more than once in one process. Adding a second
        # handler would double every line from then on, which reads as the
        # program having done each step twice.
        start_debug_log(self.directory)
        start_debug_log(self.directory)

        logging.getLogger(LOGGER_NAME).info("only once please")

        self.assertEqual(self.written().count("only once please"), 1)

    def test_it_leaves_no_handler_of_its_own_behind_when_stopped(self):
        start_debug_log(self.directory)

        stop_debug_log()

        installed = [
            handler
            for handler in logging.getLogger(LOGGER_NAME).handlers
            if handler.get_name() == logging_setup._OURS
        ]
        self.assertEqual(installed, [])


if __name__ == "__main__":
    unittest.main()
