"""Putting the result artifact where the Backend can see it.

Three steps, read from ``WorkerJobController`` and ``WorkerJobService``:

1. ``POST /api/worker/jobs/{jobId}/result-upload`` with ``fileName`` and
   ``contentType`` -- answers ``objectKey``, ``uploadUrl``, ``expiresAt``.
2. ``PUT`` the bytes at that URL.
3. ``POST .../result-upload/complete`` with ``objectKey``, ``sizeBytes`` and
   ``sha256`` -- and the Backend does not take the Worker's word for any of
   it. ``completeResultUpload`` stats the stored object, computes its own
   SHA-256, and refuses with ``RESULT_UPLOAD_MISMATCH`` unless both agree.

**The objectKey is the Backend's.** It builds ``jobs/{jobId}/`` + fileName and
checks that prefix again on completion, so a Worker cannot claim a path under
somebody else's Job. Whatever comes back from step 1 is what step 3 and
JOB_RESULT use -- this module never constructs one.

**The upload URL is a credential.** Its signature is the only thing standing
between it and anyone who can write to that key; ``LocalObjectStorage`` signs
method, key and expiry with an HMAC, and ``StorageController`` accepts the URL
with no other authentication at all. It is never logged, never put in a
message, and never in an exception.

**Nothing here reads the whole file.** An adapter is tens to hundreds of
megabytes; the PUT streams from disk with a Content-Length, and the digest was
taken a chunk at a time before that.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .logging_setup import LOGGER_NAME
from .result_artifact import ARTIFACT_CONTENT_TYPE, ARTIFACT_NAME

_debug = logging.getLogger(LOGGER_NAME)

UPLOAD_TIMEOUT_SECONDS = 900

#: Transfers are retried; verifications are not. See :func:`upload_result`.
UPLOAD_ATTEMPTS = 3
UPLOAD_FIRST_WAIT_SECONDS = 1.0
UPLOAD_WAIT_MULTIPLIER = 2.0
UPLOAD_MAX_WAIT_SECONDS = 15.0

#: How many times a fresh ticket is asked for when the one in hand is refused.
#: ``createResultUpload`` has no once-only rule -- it re-signs the same key --
#: so an expired URL is recoverable. Bounded anyway: a Backend that refuses
#: every ticket is not going to accept the fourth.
TICKET_ATTEMPTS = 2

CHUNK_BYTES = 1024 * 1024


class UploadRefused(Exception):
    """The result could not be stored. ``transient`` means it is worth another go."""

    def __init__(self, detail: str, *, transient: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.transient = transient


@dataclass(frozen=True)
class UploadTicket:
    """What the Backend answered when asked where to put the result."""

    object_key: str
    upload_url: str
    expires_at: str | None = None


@dataclass(frozen=True)
class StoredResult:
    """What the Backend confirmed it is holding."""

    object_key: str
    sha256: str
    size_bytes: int


def upload_path(job_id: str) -> str:
    return f"/api/worker/jobs/{job_id}/result-upload"


def complete_path(job_id: str) -> str:
    return f"/api/worker/jobs/{job_id}/result-upload/complete"


def check_upload_url(url: str) -> str:
    """An http(s) URL with a host, or a refusal. **Never** logged or returned.

    The same rule the dataset download uses, for the same reason: a ``file://``
    would make this process write to its own disk through an code path that
    thinks it is talking to storage, and anything more exotic hands the choice
    of protocol handler to whatever produced the field.
    """
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise UploadRefused("the upload URL is not an http(s) address")
    if not parsed.netloc:
        raise UploadRefused("the upload URL names no host")
    return url


def request_ticket(
    client: Any,
    worker_token: str,
    job_id: str,
    *,
    file_name: str = ARTIFACT_NAME,
    content_type: str = ARTIFACT_CONTENT_TYPE,
) -> UploadTicket:
    """Ask where to put it. The answer's objectKey is authoritative."""
    answered = client.request(
        "POST", upload_path(job_id),
        {"fileName": file_name, "contentType": content_type},
        bearer=worker_token)
    if not answered.succeeded:
        raise UploadRefused(_why(answered), transient=_worth_retrying(answered))

    body = answered.body
    if not isinstance(body, dict):
        raise UploadRefused("the upload ticket was not a JSON object")
    object_key = body.get("objectKey")
    upload_url = body.get("uploadUrl")
    if not isinstance(object_key, str) or not object_key:
        raise UploadRefused("the upload ticket named no objectKey")
    if not isinstance(upload_url, str) or not upload_url:
        raise UploadRefused("the upload ticket carried no URL")
    check_upload_url(upload_url)
    expires = body.get("expiresAt")
    # The key is logged; the URL never is.
    _debug.info("upload: ticket for %s", object_key)
    return UploadTicket(object_key, upload_url,
                        expires if isinstance(expires, str) else None)


def put_artifact(
    upload_url: str,
    artifact: Path,
    size_bytes: int,
    *,
    content_type: str = ARTIFACT_CONTENT_TYPE,
    opener: Callable[..., Any] | None = None,
    timeout: float = UPLOAD_TIMEOUT_SECONDS,
) -> None:
    """Stream the file to the signed URL.

    The file object is handed to urllib as the body with an explicit
    Content-Length, so it is read in pieces as the socket drains rather than
    loaded first.

    ``Content-Type`` is the one the ticket was asked for and nothing else is
    added. ``LocalObjectStorage.presignUpload`` does not sign the content type
    and ``StorageController`` does not check it, so an extra header would be a
    guess about a storage backend that does not exist yet.
    """
    check_upload_url(upload_url)
    send = opener or urllib.request.urlopen
    with artifact.open("rb") as body:
        request = urllib.request.Request(
            upload_url, data=body, method="PUT",
            headers={"Content-Type": content_type,
                     "Content-Length": str(size_bytes)})
        try:
            with send(request, timeout=timeout) as response:
                status = _status_of(response)
        except urllib.error.HTTPError as refused:
            # The status only. The URL carries a signature and the body of a
            # storage error can echo the request line back.
            raise UploadRefused(
                f"the storage refused the upload (HTTP {refused.code})",
                transient=refused.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UploadRefused(
                f"the storage could not be reached ({type(error).__name__})",
                transient=True) from None

    if status is not None and not 200 <= status < 300:
        raise UploadRefused(f"the storage answered HTTP {status}",
                            transient=status >= 500)
    _debug.info("upload: sent %s bytes", size_bytes)


def complete_upload(
    client: Any,
    worker_token: str,
    job_id: str,
    *,
    object_key: str,
    sha256: str,
    size_bytes: int,
) -> StoredResult:
    """Tell the Backend it is there, and let it check.

    A 200 here is the verification. ``completeResultUpload`` stats the object
    and compares both the size and its own SHA-256 against these values,
    refusing with ``RESULT_UPLOAD_MISMATCH`` otherwise -- so there is no
    "verified hash" field in the response to compare against, and
    ``ResultUploadCompleteResponse.verified`` is documented as always true.
    The check is that this call succeeded at all.

    The ``objectKey`` that comes back is compared anyway. It should be the one
    sent; if it is not, something between here and the row is not the object
    this Worker uploaded.
    """
    answered = client.request(
        "POST", complete_path(job_id),
        {"objectKey": object_key, "sizeBytes": int(size_bytes),
         "sha256": sha256},
        bearer=worker_token)
    if not answered.succeeded:
        raise UploadRefused(_why(answered), transient=_worth_retrying(answered))

    body = answered.body if isinstance(answered.body, dict) else {}
    confirmed = body.get("objectKey")
    if isinstance(confirmed, str) and confirmed and confirmed != object_key:
        raise UploadRefused(
            "the service confirmed a different object than the one uploaded")
    if body.get("verified") is False:
        # Not reachable through the current handler -- it throws instead of
        # answering false -- but the field is in the contract and a false here
        # would mean the object was not checked.
        raise UploadRefused("the service did not verify the uploaded result")
    _debug.info("upload: %s verified by the service", object_key)
    return StoredResult(object_key, sha256, int(size_bytes))


def upload_result(
    client: Any,
    worker_token: str,
    job_id: str,
    artifact: Path,
    *,
    sha256: str,
    size_bytes: int,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], bool] | None = None,
    attempts: int = UPLOAD_ATTEMPTS,
) -> StoredResult:
    """Ticket, PUT, complete -- with retries only where they can help.

    What is retried: a transfer that did not arrive, a 5xx, an unreachable
    service, and a ticket that was refused because it had expired -- for which
    a **new ticket** is asked for rather than the old URL being edited.

    What is not: a mismatch. If the Backend hashed the stored object and got
    something else, sending the same bytes again cannot change which of the
    two is wrong, and a Worker that kept trying until the numbers agreed would
    be a Worker that eventually uploaded something it had not checked.
    """
    waiting = UPLOAD_FIRST_WAIT_SECONDS
    ticket: UploadTicket | None = None
    tickets_used = 0
    last = ""

    for attempt in range(1, attempts + 1):
        try:
            if ticket is None:
                if tickets_used >= TICKET_ATTEMPTS:
                    raise UploadRefused(
                        "the service would not issue an upload ticket")
                tickets_used += 1
                ticket = request_ticket(client, worker_token, job_id)
            put_artifact(ticket.upload_url, artifact, size_bytes,
                         opener=opener)
            return complete_upload(
                client, worker_token, job_id, object_key=ticket.object_key,
                sha256=sha256, size_bytes=size_bytes)
        except UploadRefused as refused:
            last = refused.detail
            if not refused.transient:
                raise
            # A refused transfer may be a stale signature. The next round asks
            # for a ticket again rather than reusing a URL that may have run
            # out; asking twice for a key that already exists is harmless.
            ticket = None
            if attempt == attempts:
                break
            _debug.info("upload: %s (attempt %s/%s), waiting %.1fs",
                        last, attempt, attempts, waiting)
            if sleep is not None and not sleep(waiting):
                raise UploadRefused(
                    "the Worker is stopping, so the result was not uploaded",
                    transient=True) from None
            waiting = min(waiting * UPLOAD_WAIT_MULTIPLIER,
                          UPLOAD_MAX_WAIT_SECONDS)

    raise UploadRefused(
        f"the result could not be uploaded after {attempts} attempts ({last})",
        transient=True)


def _why(answered: Any) -> str:
    """A refusal in one line, without the token or the body's detail."""
    code = getattr(answered, "code", "") or ""
    status = getattr(answered, "status", None)
    if code:
        return f"the service refused the upload ({code})"
    if status:
        return f"the service refused the upload (HTTP {status})"
    return "the service could not be reached"


def _worth_retrying(answered: Any) -> bool:
    """5xx and unreachable, plus the refusals a fresh ticket can fix.

    401 and 403 are included **only** because the signed URL and the Worker
    token both expire, and both are re-obtainable. A 400 or a 409 is a settled
    answer about the request itself.
    """
    status = getattr(answered, "status", None)
    if status is None:
        return True                      # never reached the service
    return status in (401, 403, 408, 429) or status >= 500


def _status_of(response: Any) -> int | None:
    status = getattr(response, "status", None) or getattr(response, "code", None)
    return int(status) if isinstance(status, int) else None
