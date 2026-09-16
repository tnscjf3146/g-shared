"""Runtime-shaped first enrollment against a real local HTTP server."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker import auth_check, worker_credentials
from gshared_worker.auth_check import authenticate_worker
from gshared_worker.backend_client import BackendClient
from gshared_worker.config import WorkerSettings
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_registry import HttpWorkerRegistry
from gshared_worker.worker_wallet import prepare_wallet

from tests.test_first_run_enrollment import measured


ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
PRIVATE_KEY = "0x" + "11" * 32


class Accounts:
    def create(self):
        return ADDRESS, PRIVATE_KEY

    def address_of(self, private_key):
        if private_key != PRIVATE_KEY:
            raise ValueError("unknown key")
        return ADDRESS


class EnrollmentHandler(BaseHTTPRequestHandler):
    server: "EnrollmentServer"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}

        if self.path == "/api/auth/login":
            self.server.observed.append({"path": self.path, "status": 200})
            self._json(200, {"accessToken": "user-access-token"})
            return
        if self.path == "/api/workers/register":
            self.server.observed.append({
                "path": self.path,
                "authorized": self.headers.get("Authorization")
                == "Bearer user-access-token",
                "operatorAddress": body.get("operatorAddress"),
            })
            self._json(200, {
                "workerId": "worker-runtime-1",
                "workerToken": "worker-runtime-token",
            })
            return
        if self.path == "/api/workers/authenticate":
            self.server.observed.append({
                "path": self.path,
                "credentialPresent": bool(
                    body.get("workerId") and body.get("workerToken")
                ),
            })
            self._json(200, {"workerId": "worker-runtime-1"})
            return
        self._json(404, {"code": "NOT_FOUND", "message": "not found"})

    def log_message(self, format, *args):
        pass

    def _json(self, status, body):
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class EnrollmentServer(ThreadingHTTPServer):
    observed: list[dict]


class NewWorkerRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.server = EnrollmentServer(("127.0.0.1", 0), EnrollmentHandler)
        self.server.observed = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def test_login_200_reaches_registration_with_the_same_access_token(self):
        host, port = self.server.server_address
        origin = f"http://{host}:{port}"
        settings = WorkerSettings(
            backend_url=f"ws://{host}:{port}/ws/workers",
            worker_id=None,
            hourly_price=900,
            gpu_index=0,
            supported_workloads=(),
            worker_token=None,
            config_path=None,
        )
        facts = MachineFacts(
            device_name="DESKTOP-RUNTIME",
            os_version="Windows 11",
            gpu_model="NVIDIA GeForce RTX 4070",
            vram_mb=12282,
            driver_version="600.00",
        )
        login = BackendClient(origin).login("new@worker.test", "password")
        self.assertTrue(login.succeeded)
        access_token = login.body["accessToken"]

        load = lambda: worker_credentials.load_credentials(
            self.directory, unprotector=lambda value: value.removeprefix("sealed:")
        )

        def store(worker_id, worker_token):
            return worker_credentials.store_credentials(
                worker_id,
                worker_token,
                self.directory,
                protector=lambda value: "sealed:" + value,
            )

        def wallet(say):
            return prepare_wallet(
                say,
                self.directory,
                protector=lambda value: "sealed:" + value,
                unprotector=lambda value: value.removeprefix("sealed:"),
                accounts=Accounts(),
            )

        with patch.object(auth_check, "load_credentials", load), patch.object(
            auth_check, "store_credentials", store
        ):
            code = authenticate_worker(
                settings,
                lambda *parts: None,
                registry=HttpWorkerRegistry(BackendClient(origin)),
                ask_to_sign_in=lambda: access_token,
                describe=lambda: facts,
                benchmark=measured,
                make_wallet=wallet,
            )

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual(
            [entry["path"] for entry in self.server.observed],
            [
                "/api/auth/login",
                "/api/workers/register",
                "/api/workers/authenticate",
            ],
        )
        registration = self.server.observed[1]
        self.assertTrue(registration["authorized"])
        self.assertEqual(registration["operatorAddress"], ADDRESS)
        self.assertEqual(
            (
                self.server.observed[0]["status"],
                registration["path"] == "/api/workers/register",
            ),
            (200, True),
            "login_status=200 registration_started=true",
        )


if __name__ == "__main__":
    unittest.main()
