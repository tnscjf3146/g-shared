import unittest
from unittest.mock import AsyncMock, Mock

from gpu_worker.environment import EnvironmentManager, SetupStatus


class EnvironmentManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_docker_engine_failure_is_setup_required(self):
        provider = Mock()
        provider.snapshot.return_value = object()
        docker = AsyncMock()
        docker.check_environment.side_effect = RuntimeError("engine unavailable")

        report = await EnvironmentManager(provider, docker).inspect()

        self.assertEqual(report.status, SetupStatus.SETUP_REQUIRED)
        self.assertEqual(report.error_code, "DOCKER_ENVIRONMENT_UNAVAILABLE")

    async def test_ready_requires_host_gpu_and_container_gpu_probe(self):
        provider = Mock()
        provider.snapshot.return_value = object()
        docker = AsyncMock()

        report = await EnvironmentManager(provider, docker).inspect()

        self.assertEqual(report.status, SetupStatus.READY)
        docker.check_environment.assert_awaited_once()

