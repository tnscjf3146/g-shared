"""Reading a command's output while it is still writing it.

The Worker used to learn what a container did by waiting for it to exit and
then reading everything at once. That is fine for a probe and useless for a
training run: the one moment progress is worth knowing is the hours before
the exit.

The test that matters here is the temporal one, and it is not written with a
sleep. The child process **waits for the reader**: it prints a line and then
blocks until a file appears, and the file is created by the line callback. If
the callback only fired after the process exited, the process would never
exit and the test would time out. Nothing is timed, nothing is raced -- the
handshake either happens mid-run or the test hangs.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

from gshared_worker.command_runner import (
    AsyncSubprocessRunner,
    LineSplitter,
    TailBuffer,
)
from gshared_worker.errors import CommandError

TIMEOUT = 60.0


def run(program: str, *, timeout=TIMEOUT, max_output_bytes=64 * 1024,
        on_stdout_line=None, on_stderr_line=None):
    """The real runner, on a real child process."""
    async def go():
        return await AsyncSubprocessRunner().run_streaming(
            [sys.executable, "-u", "-c", program],
            timeout=timeout, max_output_bytes=max_output_bytes,
            on_stdout_line=on_stdout_line, on_stderr_line=on_stderr_line)

    return asyncio.run(go())


class TestLinesArriveBeforeTheProcessEnds(unittest.TestCase):
    """The whole point. Everything else here is in service of this."""

    def test_the_reader_sees_a_line_the_process_is_still_waiting_on(self):
        gate = Path(tempfile.mkdtemp()) / "seen"
        seen: list[str] = []

        def on_line(line: str) -> None:
            seen.append(line)
            if line == "first" and not gate.exists():
                gate.write_text("go", encoding="utf-8")

        # Prints, then refuses to finish until the callback has run.
        program = (
            "import os, sys, time\n"
            "print('first')\n"
            f"gate = r'{gate}'\n"
            "deadline = time.monotonic() + 30\n"
            "while not os.path.exists(gate):\n"
            "    if time.monotonic() > deadline:\n"
            "        sys.exit(9)\n"
            "    time.sleep(0.02)\n"
            "print('second')\n"
        )
        finished = run(program, on_stdout_line=on_line)

        # Exit 9 is the child giving up on the reader. Zero means the file
        # appeared, which only the callback can have done, which means the
        # callback ran while the process was alive.
        self.assertEqual(finished.exit_code, 0, "the reader never reached it")
        self.assertEqual(seen, ["first", "second"])

    def test_every_line_arrives_in_order(self):
        seen: list[str] = []
        program = "\n".join(f"print({n})" for n in range(50))

        run(program, on_stdout_line=seen.append)

        self.assertEqual(seen, [str(n) for n in range(50)])

    def test_the_finished_output_still_holds_what_was_streamed(self):
        finished = run("print('a')\nprint('b')")

        self.assertEqual(finished.stdout.split(), ["a", "b"])


class TestBothPipesAreDrained(unittest.TestCase):
    """A reader that took one pipe at a time would hang on the other.

    The failure is not subtle once it happens and is invisible until it does:
    the child fills the stderr buffer, blocks writing, and stops producing the
    stdout the reader is waiting for. Neither side moves again.
    """

    def test_a_flood_of_stderr_does_not_stop_the_command(self):
        program = (
            "import sys\n"
            "for n in range(20000):\n"
            "    print('x' * 60, file=sys.stderr)\n"
            "print('done')\n"
        )
        out: list[str] = []

        finished = run(program, max_output_bytes=256 * 1024,
                       on_stdout_line=out.append)

        self.assertEqual(finished.exit_code, 0)
        self.assertIn("done", out)

    def test_stderr_lines_are_reported_too(self):
        errors: list[str] = []

        run("import sys\nprint('bad', file=sys.stderr)",
            on_stderr_line=errors.append)

        self.assertEqual(errors, ["bad"])


class TestMemoryIsBounded(unittest.TestCase):
    def test_a_talkative_command_is_kept_to_its_limit(self):
        program = "for n in range(20000):\n    print('y' * 80)\n"
        counted = []

        finished = run(program, max_output_bytes=8 * 1024,
                       on_stdout_line=lambda line: counted.append(1))

        self.assertLessEqual(len(finished.stdout), 8 * 1024)
        # Every line was still read and offered -- only the record is trimmed.
        self.assertEqual(len(counted), 20000)

    def test_the_oldest_output_is_what_goes(self):
        tail = TailBuffer(8)
        tail.add(b"0123456789")

        self.assertEqual(tail.text(), "23456789")
        self.assertTrue(tail.dropped)

    def test_exceeding_the_limit_does_not_fail_the_command(self):
        """The difference between this path and ``run``, on purpose.

        ``run`` refuses output past its limit, which is right for a probe: a
        diagnosis that produced a gigabyte has gone wrong. Doing that here
        would throw away hours of finished GPU work because a CUDA warning
        repeated, and the events that mattered were read live long before.
        """
        program = "for n in range(5000):\n    print('z' * 80)\n"

        finished = run(program, max_output_bytes=4 * 1024)

        self.assertEqual(finished.exit_code, 0)

    def test_run_still_refuses_output_past_its_limit(self):
        # The old behaviour, unchanged. This is the regression guard.
        async def go():
            return await AsyncSubprocessRunner().run(
                [sys.executable, "-u", "-c", "print('z' * 5000)"],
                timeout=TIMEOUT, max_output_bytes=64)

        with self.assertRaises(CommandError) as raised:
            asyncio.run(go())

        self.assertEqual(raised.exception.code, "OUTPUT_LIMIT_EXCEEDED")


class TestTheOldContractIsKept(unittest.TestCase):
    def test_the_exit_code_comes_back(self):
        self.assertEqual(run("import sys; sys.exit(13)").exit_code, 13)

    def test_a_command_that_hangs_is_killed(self):
        started = time.monotonic()

        with self.assertRaises(CommandError) as raised:
            run("import time; time.sleep(60)", timeout=1.0)

        self.assertEqual(raised.exception.code, "COMMAND_TIMEOUT")
        self.assertLess(time.monotonic() - started, 30,
                        "the timeout did not actually stop it")

    def test_a_command_that_is_not_there_is_named(self):
        async def go():
            return await AsyncSubprocessRunner().run_streaming(
                ["gshared-no-such-program"], timeout=5, max_output_bytes=1024)

        with self.assertRaises(CommandError) as raised:
            asyncio.run(go())

        self.assertEqual(raised.exception.code, "COMMAND_NOT_FOUND")

    def test_nothing_is_run_through_a_shell(self):
        import ast
        import inspect

        from gshared_worker import command_runner

        tree = ast.parse(inspect.getsource(command_runner))
        shells = [node for node in ast.walk(tree)
                  if isinstance(node, ast.keyword) and node.arg == "shell"]
        self.assertEqual(shells, [])
        self.assertNotIn("create_subprocess_shell",
                         inspect.getsource(command_runner))


class TestAReaderThatMisbehaves(unittest.TestCase):
    def test_a_callback_that_raises_does_not_stop_the_command(self):
        """A bug in the progress counter must not cost a training run.

        If a raising callback stopped the pipe being drained, the container
        would block on a full buffer and the GPU would sit there.
        """
        seen: list[str] = []

        def on_line(line: str) -> None:
            seen.append(line)
            raise RuntimeError("boom")

        finished = run("print('a')\nprint('b')", on_stdout_line=on_line)

        self.assertEqual(finished.exit_code, 0)
        self.assertEqual(seen, ["a", "b"])


class TestSplittingLines(unittest.TestCase):
    def test_a_terminator_split_across_two_chunks_still_works(self):
        splitter = LineSplitter()

        self.assertEqual(splitter.feed(b"hello"), [])
        self.assertEqual(splitter.feed(b" world\n"), [b"hello world"])

    def test_crlf_is_one_terminator_not_two(self):
        # The first draft published a blank line after every real one.
        self.assertEqual(LineSplitter().feed(b"a\r\nb\r\n"), [b"a", b"b"])

    def test_a_carriage_return_at_the_edge_waits_for_its_pair(self):
        splitter = LineSplitter()

        self.assertEqual(splitter.feed(b"a\r"), [])
        self.assertEqual(splitter.feed(b"\nb\n"), [b"a", b"b"])

    def test_a_progress_bar_redraw_is_read_as_lines(self):
        # tqdm and transformers return to the start of the line instead of
        # ending it. Without this the whole run is one unfinished line.
        self.assertEqual(LineSplitter().feed(b"10%\r20%\r30%\r\n"),
                         [b"10%", b"20%", b"30%"])

    def test_a_line_that_never_ends_is_handed_over_anyway(self):
        splitter = LineSplitter(limit=4)

        self.assertEqual(splitter.feed(b"abcdefghij"), [b"abcd", b"efgh"])
        self.assertEqual(splitter.flush(), [b"ij"])

    def test_a_closed_pipe_gives_up_what_it_held(self):
        splitter = LineSplitter()
        splitter.feed(b"unterminated")

        self.assertEqual(splitter.flush(), [b"unterminated"])
        self.assertEqual(splitter.flush(), [])


if __name__ == "__main__":
    unittest.main()
