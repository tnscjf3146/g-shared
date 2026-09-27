"""The benchmark's peak VRAM reaches the Backend instead of being thrown away.

The benchmark container has always reported ``peakVramMb`` and the Worker has
always parsed it -- and then dropped it, because the registration request had
nowhere to put it. So the service only ever knew the card's reported capacity
(``vramMb``), which includes what the driver and the display already hold.

The field is **optional on the wire**: a Backend that has no column for it yet
must keep accepting registrations, so an unknown measurement is left out of the
request rather than sent as null.
"""

from __future__ import annotations

import json
import unittest

from gshared_worker.benchmark import BenchmarkOutcome, BenchmarkReport, BenchmarkResult
from gshared_worker.machine_facts import MachineFacts
from gshared_worker.worker_registry import (
    BenchmarkUpdate,
    WorkerRegistrationRequest,
)

FACTS = MachineFacts(
    device_name="DESKTOP-TEST", os_version="Windows 10.0.26200",
    gpu_model="NVIDIA GeForce RTX 4050 Laptop GPU", vram_mb=6141,
    driver_version="596.36")

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def registration(**changes) -> WorkerRegistrationRequest:
    values = dict(
        operator_address=OPERATOR, client_version="0.1.0",
        benchmark_version="gshared-benchmark-v1")
    values.update(changes)
    peak = values.pop("peak_vram_mb", 2172)
    return WorkerRegistrationRequest.describing(
        FACTS, 41.5, peak_vram_mb=peak, **values)


class TestRegistering(unittest.TestCase):
    def test_the_measured_peak_goes_with_the_benchmark(self):
        payload = registration().as_payload()

        self.assertEqual(payload["benchmark"]["peakVramMb"], 2172)

    def test_it_is_beside_the_throughput_not_beside_the_card(self):
        # gpu.vramMb is the card; the peak is a property of the measurement.
        payload = registration().as_payload()

        self.assertNotIn("peakVramMb", payload["gpu"])
        self.assertEqual(payload["gpu"]["vramMb"], 6141)

    def test_an_unknown_measurement_is_left_out_rather_than_sent_as_null(self):
        payload = registration(peak_vram_mb=None).as_payload()

        self.assertNotIn("peakVramMb", payload["benchmark"])
        self.assertNotIn("null", json.dumps(payload["benchmark"]))

    def test_the_rest_of_the_request_is_unchanged(self):
        payload = registration(peak_vram_mb=None).as_payload()

        self.assertEqual(payload["benchmark"], {
            "benchmarkTokensPerSec": 41.5,
            "benchmarkVersion": "gshared-benchmark-v1"})

    def test_it_is_a_whole_number_of_mib(self):
        payload = registration(peak_vram_mb=2172.0).as_payload()

        self.assertIsInstance(payload["benchmark"]["peakVramMb"], int)


class TestReMeasuring(unittest.TestCase):
    """``PUT /api/workers/{id}/benchmark`` carries the same measurement."""

    def test_the_update_carries_it_too(self):
        payload = registration().benchmark_update().as_payload()

        self.assertEqual(payload["peakVramMb"], 2172)
        self.assertEqual(payload["benchmarkTokensPerSec"], 41.5)

    def test_an_unknown_measurement_is_left_out_there_as_well(self):
        payload = BenchmarkUpdate("RTX 4050", 6141, 41.5, "v1").as_payload()

        self.assertNotIn("peakVramMb", payload)

    def test_the_default_keeps_older_callers_working(self):
        # Four positional arguments is how every caller built this before.
        update = BenchmarkUpdate("RTX 4050", 6141, 41.5, "v1")

        self.assertIsNone(update.peak_vram_mb)


class TestWhereTheNumberComesFrom(unittest.TestCase):
    def test_the_benchmark_result_already_holds_it(self):
        report = BenchmarkReport(
            BenchmarkOutcome.MEASURED, "measured",
            BenchmarkResult(tokens_per_sec=41.5, processed_tokens=122880,
                            elapsed_seconds=98.3, measured_steps=60,
                            batch_size=4, sequence_length=512,
                            run_rates=(41.5,), peak_vram_mb=2172))

        self.assertEqual(report.result.peak_vram_mb, 2172)

    def test_the_enrolment_hands_it_to_the_registration(self):
        from gshared_worker import auth_check

        from tests.test_job_progress import code_only

        self.assertIn("peak_vram_mb=measured.result.peak_vram_mb",
                      code_only(auth_check))


if __name__ == "__main__":
    unittest.main()
