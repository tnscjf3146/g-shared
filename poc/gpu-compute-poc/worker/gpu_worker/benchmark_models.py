"""Validated benchmark v1 result shared by cache and REGISTER construction."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .errors import BenchmarkError

BENCHMARK_VERSION = "gpu-benchmark-v1"
MATRIX_SIZE = 4096
PRECISION = "FP16"
WARMUP_SECONDS = 3
MEASUREMENT_SECONDS = 20


@dataclass(frozen=True)
class BenchmarkResult:
    benchmark_version: str
    gpu_uuid: str
    gpu_model: str
    vram_mb: int
    matrix_size: int
    precision: str
    warmup_seconds: int
    measurement_seconds: int
    iterations: int
    elapsed_ms: int
    throughput_tflops: float
    benchmark_score: float
    measured_at: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "BenchmarkResult":
        try:
            result = cls(
                benchmark_version=_string(payload, "benchmarkVersion"),
                gpu_uuid=_string(payload, "gpuUuid"),
                gpu_model=_string(payload, "gpuModel"),
                vram_mb=_integer(payload, "vramMb"),
                matrix_size=_integer(payload, "matrixSize"),
                precision=_string(payload, "precision"),
                warmup_seconds=_integer(payload, "warmupSeconds"),
                measurement_seconds=_integer(payload, "measurementSeconds"),
                iterations=_integer(payload, "iterations"),
                elapsed_ms=_integer(payload, "elapsedMs"),
                throughput_tflops=_number(payload, "throughputTflops"),
                benchmark_score=_number(payload, "benchmarkScore"),
                measured_at=_string(payload, "measuredAt"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BenchmarkError("BENCHMARK_INVALID_RESULT", str(error)) from error
        result.validate()
        return result

    def validate(self) -> None:
        if self.benchmark_version != BENCHMARK_VERSION:
            raise _invalid("unsupported benchmarkVersion")
        if not self.gpu_uuid.strip():
            raise _invalid("gpuUuid is required")
        if not self.gpu_model.strip():
            raise _invalid("gpuModel is required")
        if self.vram_mb <= 0:
            raise _invalid("vramMb must be greater than zero")
        if self.matrix_size != MATRIX_SIZE:
            raise _invalid("matrixSize must be 4096")
        if self.precision != PRECISION:
            raise _invalid("precision must be FP16")
        if self.warmup_seconds != WARMUP_SECONDS:
            raise _invalid("warmupSeconds must be 3")
        if self.measurement_seconds != MEASUREMENT_SECONDS:
            raise _invalid("measurementSeconds must be 20")
        if self.iterations <= 0:
            raise _invalid("iterations must be greater than zero")
        if self.elapsed_ms <= 0:
            raise _invalid("elapsedMs must be greater than zero")
        if not math.isfinite(self.throughput_tflops) or self.throughput_tflops <= 0:
            raise _invalid("throughputTflops must be finite and greater than zero")
        if not math.isfinite(self.benchmark_score) or self.benchmark_score <= 0:
            raise _invalid("benchmarkScore must be finite and greater than zero")
        if self.benchmark_score != self.throughput_tflops:
            raise _invalid("benchmarkScore must equal throughputTflops")
        try:
            measured_at = datetime.fromisoformat(self.measured_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise _invalid("measuredAt must be ISO-8601") from error
        if measured_at.tzinfo is None:
            raise _invalid("measuredAt must include a timezone")

    def to_cache_payload(self) -> dict[str, object]:
        return {
            "benchmarkVersion": self.benchmark_version,
            "gpuUuid": self.gpu_uuid,
            "gpuModel": self.gpu_model,
            "vramMb": self.vram_mb,
            "matrixSize": self.matrix_size,
            "precision": self.precision,
            "warmupSeconds": self.warmup_seconds,
            "measurementSeconds": self.measurement_seconds,
            "iterations": self.iterations,
            "elapsedMs": self.elapsed_ms,
            "throughputTflops": self.throughput_tflops,
            "benchmarkScore": self.benchmark_score,
            "measuredAt": self.measured_at,
        }

    def to_register_payload(self) -> dict[str, object]:
        return {
            "version": self.benchmark_version,
            "score": self.benchmark_score,
            "throughputTflops": self.throughput_tflops,
            "matrixSize": self.matrix_size,
            "precision": self.precision,
            "measuredAt": self.measured_at,
        }


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _number(payload: Mapping[str, object], key: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def _invalid(message: str) -> BenchmarkError:
    return BenchmarkError("BENCHMARK_INVALID_RESULT", message)
