"""Strict STEP 2.5 EXECUTE protocol parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from .errors import WorkerProtocolError


@dataclass(frozen=True)
class ExecuteRequest:
    job_id: str
    workload: str


def decode_execute(raw: str) -> ExecuteRequest:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise WorkerProtocolError("message must be valid JSON") from error
    if not isinstance(payload, dict):
        raise WorkerProtocolError("message must be a JSON object")
    if set(payload) != {"type", "jobId", "workload"}:
        raise WorkerProtocolError("EXECUTE contains unexpected or missing fields")
    if payload["type"] != "EXECUTE":
        raise WorkerProtocolError("unsupported Backend message type")
    try:
        job_id = str(UUID(payload["jobId"]))
    except (TypeError, ValueError, AttributeError) as error:
        raise WorkerProtocolError("jobId must be a UUID") from error
    workload = payload["workload"]
    if not isinstance(workload, str) or not workload or workload.isspace():
        raise WorkerProtocolError("workload must not be blank")
    return ExecuteRequest(job_id, workload)
