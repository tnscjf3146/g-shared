"""Running a real command, and reading what a Windows tool actually writes."""

from __future__ import annotations

import sys
import unittest

from gshared_worker.command_runner import AsyncSubprocessRunner, decode_output
from gshared_worker.errors import CommandError, WorkerError


class WhatTheToolWroteTest(unittest.TestCase):
    def test_plain_utf8_is_read_as_utf8(self):
        self.assertEqual(decode_output("Default Version: 2\n".encode("utf-8")),
                         "Default Version: 2\n")

    def test_nothing_at_all_is_an_empty_string(self):
        self.assertEqual(decode_output(b""), "")

    def test_utf16_with_a_byte_order_mark_is_read_as_utf16(self):
        self.assertEqual(decode_output("기본 버전: 2".encode("utf-16")), "기본 버전: 2")

    def test_utf16_without_a_mark_is_recognised_by_its_nul_bytes(self):
        # wsl.exe writes this. Read as UTF-8 it decodes without complaint --
        # a NUL is a valid character -- and every plain substring test then
        # fails on text that is really there.
        self.assertEqual(decode_output("--name".encode("utf-16le")), "--name")

    def test_korean_utf16_survives_rather_than_being_destroyed(self):
        # Reading it as UTF-8 keeps the ASCII by accident and loses everything
        # else, which is how a Korean Windows once reported a failure whose
        # reason could not be read.
        self.assertEqual(decode_output("설치되지 않음".encode("utf-16le")), "설치되지 않음")

    def test_bytes_that_are_neither_are_still_readable_text(self):
        self.assertIsInstance(decode_output(b"\xff\xfe\x00\xd8"), str)


class RunningARealCommandTest(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        return AsyncSubprocessRunner()

    async def test_it_reports_what_the_command_printed_and_its_exit_code(self):
        result = await self.runner().run(
            [sys.executable, "-c", "print('hello')"], timeout=30, max_output_bytes=4096
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.stdout)

    async def test_a_failing_command_is_a_result_not_an_exception(self):
        # The probe has to tell "the tool said no" from "the tool is not there",
        # so a non-zero exit is data.
        result = await self.runner().run(
            [sys.executable, "-c", "raise SystemExit(3)"],
            timeout=30, max_output_bytes=4096,
        )

        self.assertEqual(result.exit_code, 3)

    async def test_stderr_is_kept_separately(self):
        result = await self.runner().run(
            [sys.executable, "-c", "import sys; sys.stderr.write('bad')"],
            timeout=30, max_output_bytes=4096,
        )

        self.assertIn("bad", result.stderr)
        self.assertEqual(result.stdout, "")

    async def test_a_tool_that_is_not_installed_is_named_as_such(self):
        with self.assertRaises(CommandError) as refused:
            await self.runner().run(
                ["gshared-no-such-tool"], timeout=30, max_output_bytes=4096
            )

        self.assertEqual(refused.exception.code, "COMMAND_NOT_FOUND")

    async def test_a_command_that_hangs_is_killed_and_reported(self):
        # Without this the Worker never starts, and the supplier is looking at a
        # console that has printed one line and stopped.
        with self.assertRaises(CommandError) as refused:
            await self.runner().run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=1, max_output_bytes=4096,
            )

        self.assertEqual(refused.exception.code, "COMMAND_TIMEOUT")

    async def test_a_command_that_says_far_too_much_is_refused(self):
        with self.assertRaises(CommandError) as refused:
            await self.runner().run(
                [sys.executable, "-c", "print('x' * 100000)"],
                timeout=30, max_output_bytes=1024,
            )

        self.assertEqual(refused.exception.code, "OUTPUT_LIMIT_EXCEEDED")

    async def test_every_failure_is_one_the_worker_already_knows_how_to_treat(self):
        # The probe catches WorkerError and answers None. A CommandError that
        # did not inherit it would escape as an unexpected fault instead.
        self.assertTrue(issubclass(CommandError, WorkerError))


if __name__ == "__main__":
    unittest.main()
