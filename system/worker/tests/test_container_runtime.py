"""Running a container and asking the GPU what it can see.

The point of this layer is that exit code 0 is not the answer. Most of the file
is about telling apart the ways it can fail, because they point at different
fixes: an engine that is down is not a GPU that is unreachable.
"""

from __future__ import annotations

import unittest

from gshared_worker.command_runner import CommandResult
from gshared_worker.container_runtime import (
    GPU_PROBE_IMAGE,
    ContainerGpuProbe,
    GpuOutcome,
    parse_gpu_report,
)
from gshared_worker.errors import CommandError

HEALTHY = {
    "docker version": CommandResult(0, "27.3.1\n", ""),
    "docker info": CommandResult(0, "linux\n", ""),
    "docker image inspect": CommandResult(0, '"sha256:abc"\n', ""),
    "docker run": CommandResult(
        0, "NVIDIA GeForce RTX 4070 Laptop GPU, 596.36, 8188\n", ""),
}


class Docker:
    """Answers per Docker sub-command and remembers what was run."""

    def __init__(self, **overrides):
        self.answers = {**HEALTHY, **overrides}
        self.commands: list[list[str]] = []

    async def run(self, args, *, timeout, max_output_bytes):
        self.commands.append(list(args))
        joined = " ".join(args)
        for needle, answer in self.answers.items():
            if joined.startswith(needle):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return CommandResult(0, "", "")

    @property
    def flattened(self) -> str:
        return "\n".join(" ".join(c) for c in self.commands)


async def verify(**overrides):
    docker = Docker(**overrides)
    return await ContainerGpuProbe(docker).verify(), docker


class AGpuAContainerCanSeeTest(unittest.IsolatedAsyncioTestCase):
    async def test_it_succeeds(self):
        verified, _ = await verify()

        self.assertTrue(verified.succeeded)
        self.assertIs(verified.outcome, GpuOutcome.VISIBLE)

    async def test_it_reports_the_model_the_driver_and_the_vram(self):
        verified, _ = await verify()

        self.assertEqual(verified.report.model, "NVIDIA GeForce RTX 4070 Laptop GPU")
        self.assertEqual(verified.report.driver_version, "596.36")
        self.assertEqual(verified.report.vram_mib, 8188)

    async def test_the_summary_names_all_three(self):
        verified, _ = await verify()

        self.assertIn("RTX 4070", verified.detail)
        self.assertIn("596.36", verified.detail)
        self.assertIn("8188", verified.detail)

    async def test_the_container_is_thrown_away_and_cannot_reach_the_network(self):
        _, docker = await verify()

        run = next(c for c in docker.commands if c[:2] == ["docker", "run"])
        for flag in ("--rm", "--network", "none", "--read-only",
                     "--cap-drop", "ALL", "--gpus", "all"):
            self.assertIn(flag, run)

    async def test_the_probe_image_is_pinned_by_digest(self):
        # A moving tag would let the check drift under us.
        self.assertIn("@sha256:", GPU_PROBE_IMAGE)


class TheWaysItCanFailAreNotTheSameTest(unittest.IsolatedAsyncioTestCase):
    async def test_an_engine_that_does_not_answer_is_not_a_gpu_fault(self):
        verified, docker = await verify(
            **{"docker version": CommandResult(1, "", "cannot connect")})

        self.assertIs(verified.outcome, GpuOutcome.ENGINE_UNAVAILABLE)
        # Nothing was pulled and no container was started.
        self.assertNotIn("docker run", docker.flattened)
        self.assertNotIn("docker pull", docker.flattened)

    async def test_windows_containers_are_named_as_such(self):
        verified, _ = await verify(
            **{"docker info": CommandResult(0, "windows\n", "")})

        self.assertIs(verified.outcome, GpuOutcome.LINUX_REQUIRED)

    async def test_an_image_that_cannot_be_fetched_says_nothing_about_the_gpu(self):
        # Calling a network problem a GPU fault sends somebody after their
        # driver for something a driver cannot fix.
        verified, _ = await verify(
            **{"docker image inspect": CommandResult(1, "", "No such image"),
               "docker pull": CommandResult(1, "", "connection refused")})

        self.assertIs(verified.outcome, GpuOutcome.IMAGE_UNAVAILABLE)

    async def test_a_container_that_could_not_see_the_gpu_is_a_gpu_fault(self):
        verified, _ = await verify(
            **{"docker run": CommandResult(125, "", "could not select device driver")})

        self.assertIs(verified.outcome, GpuOutcome.GPU_UNAVAILABLE)

    async def test_exit_zero_with_nothing_printed_is_not_success(self):
        # The container started and proved nothing. Treating that as a pass is
        # exactly how a Worker comes online without a usable GPU.
        verified, _ = await verify(**{"docker run": CommandResult(0, "\n", "")})

        self.assertFalse(verified.succeeded)
        self.assertIs(verified.outcome, GpuOutcome.GPU_UNAVAILABLE)

    async def test_exit_zero_with_an_unparsable_line_is_not_success(self):
        verified, _ = await verify(
            **{"docker run": CommandResult(0, "no devices were found\n", "")})

        self.assertFalse(verified.succeeded)

    async def test_a_distribution_that_would_not_run_docker_at_all(self):
        verified, _ = await verify(
            **{"docker version": CommandError("COMMAND_NOT_FOUND", "no wsl.exe")})

        self.assertIs(verified.outcome, GpuOutcome.ENGINE_UNAVAILABLE)

    async def test_a_probe_that_hangs_is_a_gpu_fault_not_a_crash(self):
        verified, _ = await verify(
            **{"docker run": CommandError("COMMAND_TIMEOUT", "too slow")})

        self.assertIs(verified.outcome, GpuOutcome.GPU_UNAVAILABLE)

    async def test_no_expected_outcome_escapes_as_an_exception(self):
        for override in (
            {"docker version": CommandResult(1, "", "")},
            {"docker info": CommandResult(1, "", "")},
            {"docker run": CommandResult(1, "", "")},
            {"docker version": CommandError("COMMAND_TIMEOUT", "slow")},
        ):
            verified, _ = await verify(**override)
            self.assertIn(verified.outcome, list(GpuOutcome))


class AnImageThatIsAlreadyThereIsNotPulledAgainTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_cached_image_costs_no_download(self):
        _, docker = await verify()

        self.assertNotIn("docker pull", docker.flattened)

    async def test_a_missing_image_is_pulled_once_then_confirmed(self):
        seen = {"inspect": 0}

        class Once(Docker):
            async def run(self, args, *, timeout, max_output_bytes):
                joined = " ".join(args)
                if joined.startswith("docker image inspect"):
                    seen["inspect"] += 1
                    self.commands.append(list(args))
                    return CommandResult(0 if seen["inspect"] > 1 else 1, "", "")
                return await super().run(args, timeout=timeout,
                                         max_output_bytes=max_output_bytes)

        docker = Once()
        verified = await ContainerGpuProbe(docker).verify()

        self.assertTrue(verified.succeeded)
        self.assertEqual(sum("pull" in c for c in docker.commands), 1)
        # Asked for again rather than assumed: a pull that returned zero and
        # left no image would otherwise be found out by the container instead.
        self.assertEqual(seen["inspect"], 2)


class ReadingWhatNvidiaSmiPrintedTest(unittest.TestCase):
    def test_a_normal_line(self):
        report = parse_gpu_report("NVIDIA GeForce RTX 4070 Laptop GPU, 596.36, 8188\n")

        self.assertEqual(report.model, "NVIDIA GeForce RTX 4070 Laptop GPU")
        self.assertEqual(report.driver_version, "596.36")
        self.assertEqual(report.vram_mib, 8188)

    def test_the_first_of_several_gpus_is_enough(self):
        # The question is whether a GPU is reachable, not how many there are.
        report = parse_gpu_report("First GPU, 596.36, 8188\nSecond GPU, 596.36, 8188\n")

        self.assertEqual(report.model, "First GPU")

    def test_a_line_without_vram_still_names_the_gpu(self):
        report = parse_gpu_report("NVIDIA A100, 550.54\n")

        self.assertEqual(report.model, "NVIDIA A100")
        self.assertIsNone(report.vram_mib)

    def test_nothing_printed_is_no_report(self):
        self.assertIsNone(parse_gpu_report(""))

    def test_a_message_that_is_not_a_gpu_is_no_report(self):
        self.assertIsNone(parse_gpu_report("No devices were found\n"))

    def test_utf16_nuls_do_not_hide_the_answer(self):
        report = parse_gpu_report("N\x00V\x00,\x00 \x001\x00\n")

        self.assertIsNotNone(report)


if __name__ == "__main__":
    unittest.main()
