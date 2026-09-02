"""Current-user Named Pipe control surface offering exactly two commands.

The Worker exposes no loopback TCP port. The pipe is reachable only by the SID
that started the Worker, refuses remote clients, and accepts a size-capped typed
message with a closed field allowlist. Nothing from the Worker configuration
crosses it, and it runs nothing: the only effects are reading a lifecycle status
and asking the Worker to stop itself.

Every pipe operation is overlapped and bounded by a deadline, and every wait also
watches a stop event. A local caller that stalls mid-request, never terminates
its request, or never reads its answer is dropped when its deadline passes; it
can neither wedge the single pipe instance nor keep the Worker from exiting.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import threading
import time
from typing import Callable, Protocol

from .instance_guard import WindowsMutexPlatform, validate_worker_identity


ALLOWED_COMMANDS = frozenset({"STATUS", "GRACEFUL_SHUTDOWN"})
MAX_REQUEST_BYTES = 4096
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 5.0

_SID = re.compile(r"^S-1-\d{1,10}(?:-\d{1,10}){1,15}$")
_LIFECYCLE_STATUSES = frozenset({"STARTING", "IDLE", "BUSY", "OFFLINE"})
_PIPE_NAME_DOMAIN = "gpu-compute-worker-control-pipe"

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
_FILE_FLAG_OVERLAPPED = 0x40000000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_WAIT_OBJECT_0 = 0
_MAX_WAIT_MILLISECONDS = 0xFFFFFFFE


def worker_control_pipe_name(sid: str, worker_id: str, gpu_index: int) -> str:
    """Derive the pipe name from the install identity, never from raw values.

    The digest is domain-separated from the instance mutex because the pipe
    namespace is world-enumerable: anyone who lists it must not thereby learn the
    Worker's mutex name.
    """
    validate_worker_identity(worker_id, gpu_index)
    if not isinstance(sid, str) or not sid.strip():
        raise ValueError("sid must be a non-empty string")
    digest = sha256(
        "\x1f".join((_PIPE_NAME_DOMAIN, sid, worker_id, str(gpu_index))).encode("utf-8")
    ).hexdigest()
    return f"\\\\.\\pipe\\GPUComputeWorkerControl-{digest}"


def owner_only_security_descriptor(sid: object) -> str:
    """Build a protected DACL granting read/write to the owning SID and no one else."""
    if not isinstance(sid, str) or not _SID.fullmatch(sid):
        raise ValueError("owner SID is not a canonical SID string")
    return f"D:P(A;;GRGW;;;{sid})"


def current_user_sid() -> str:
    return WindowsMutexPlatform().current_user_sid()


@dataclass(frozen=True)
class LocalControlRequest:
    """One decoded request: a command and the verified caller identity."""

    command: str
    peer_sid: str | None


@dataclass(frozen=True)
class LocalControlResponse:
    """One answer, carrying a stable code and nothing about the request."""

    code: str
    status: str | None = None

    def to_payload(self) -> bytes:
        body: dict[str, object] = {"code": self.code}
        if self.status is not None:
            body["status"] = self.status
        return json.dumps(body, separators=(",", ":")).encode("utf-8")


class ShutdownSignal(Protocol):
    def set(self) -> None: ...


class LocalControlServer:
    """Decides every request; it never runs a command or touches configuration."""

    def __init__(
        self,
        status_provider: Callable[[], str],
        shutdown: ShutdownSignal,
        *,
        owner_sid: str,
    ) -> None:
        if not isinstance(owner_sid, str) or not _SID.fullmatch(owner_sid):
            raise ValueError("owner SID is not a canonical SID string")
        self._status_provider = status_provider
        self._shutdown = shutdown
        self.owner_sid = owner_sid

    def handle(self, request: LocalControlRequest) -> LocalControlResponse:
        # Identity first: a caller who is not the owner learns nothing else,
        # not even whether the command it named exists.
        if request.peer_sid != self.owner_sid:
            return LocalControlResponse("ACCESS_DENIED")
        if request.command not in ALLOWED_COMMANDS:
            return LocalControlResponse("COMMAND_NOT_ALLOWED")
        if request.command == "GRACEFUL_SHUTDOWN":
            self._shutdown.set()
            return LocalControlResponse("OK")
        return LocalControlResponse("OK", status=self._lifecycle())

    def handle_payload(self, payload: bytes, peer_sid: str | None) -> bytes:
        return self._answer(payload, peer_sid).to_payload()

    def _answer(self, payload: bytes, peer_sid: str | None) -> LocalControlResponse:
        # The peer is judged before the payload, so a stranger cannot tell a refused
        # command from a malformed or oversized one.
        if peer_sid != self.owner_sid:
            return LocalControlResponse("ACCESS_DENIED")
        if len(payload) > MAX_REQUEST_BYTES:
            return LocalControlResponse("REQUEST_TOO_LARGE")
        command = _decode_command(payload)
        if command is None:
            return LocalControlResponse("MALFORMED_REQUEST")
        return self.handle(LocalControlRequest(command=command, peer_sid=peer_sid))

    def _lifecycle(self) -> str:
        try:
            value = self._status_provider()
        except Exception:
            return "UNKNOWN"
        return value if value in _LIFECYCLE_STATUSES else "UNKNOWN"


def _decode_command(payload: bytes) -> str | None:
    """Accept exactly {"command": "<string>"}; any other shape is malformed."""
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(message, dict) or set(message) != {"command"}:
        return None
    command = message["command"]
    return command if isinstance(command, str) else None


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _PipeApi:
    """The Windows entry points both ends of the control channel share."""

    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        overlapped = ctypes.POINTER(_Overlapped)
        self.kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.kernel32.CreateNamedPipeW.restype = ctypes.c_void_p
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.kernel32.CreateFileW.restype = ctypes.c_void_p
        self.kernel32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, overlapped]
        self.kernel32.ConnectNamedPipe.restype = wintypes.BOOL
        self.kernel32.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
        self.kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
        self.kernel32.GetNamedPipeServerProcessId.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.GetNamedPipeServerProcessId.restype = wintypes.BOOL
        self.kernel32.ReadFile.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), overlapped,
        ]
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.kernel32.WriteFile.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), overlapped,
        ]
        self.kernel32.WriteFile.restype = wintypes.BOOL
        self.kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        self.kernel32.CreateEventW.restype = ctypes.c_void_p
        self.kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        self.kernel32.SetEvent.restype = wintypes.BOOL
        self.kernel32.ResetEvent.argtypes = [ctypes.c_void_p]
        self.kernel32.ResetEvent.restype = wintypes.BOOL
        self.kernel32.WaitForMultipleObjects.argtypes = [
            wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        ]
        self.kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
        self.kernel32.GetOverlappedResult.argtypes = [
            ctypes.c_void_p, overlapped, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        ]
        self.kernel32.GetOverlappedResult.restype = wintypes.BOOL
        self.kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, overlapped]
        self.kernel32.CancelIoEx.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p
        self.kernel32.GetCurrentThread.restype = ctypes.c_void_p
        self.advapi32.ImpersonateNamedPipeClient.argtypes = [ctypes.c_void_p]
        self.advapi32.ImpersonateNamedPipeClient.restype = wintypes.BOOL
        self.advapi32.RevertToSelf.restype = wintypes.BOOL
        self.advapi32.OpenThreadToken.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.OpenThreadToken.restype = wintypes.BOOL
        convert = self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
        ]
        convert.restype = wintypes.BOOL

    def create_event(self) -> object:
        handle = self.kernel32.CreateEventW(None, True, False, None)
        if not handle:
            _raise_last_error("CreateEventW")
        return handle

    def close(self, handle: object) -> None:
        if handle:
            self.kernel32.CloseHandle(handle)


class _BoundedPipeChannel:
    """One pipe handle driven with overlapped I/O under a caller-supplied deadline."""

    def __init__(self, api: _PipeApi, handle: object, stop_event: object) -> None:
        self._api = api
        self._handle = handle
        self._stop_event = stop_event
        self._io_event: object | None = api.create_event()

    def close(self) -> None:
        handle, self._io_event = self._io_event, None
        self._api.close(handle)

    def connect(self, deadline: float) -> bool:
        overlapped = self._begin()
        ctypes.set_last_error(0)
        started = self._api.kernel32.ConnectNamedPipe(
            self._handle, ctypes.byref(overlapped)
        )
        if not started and ctypes.get_last_error() == _ERROR_PIPE_CONNECTED:
            return True
        return self._settle(started, overlapped, deadline) is not None

    def read(self, size: int, deadline: float) -> bytes | None:
        buffer = ctypes.create_string_buffer(size)
        overlapped = self._begin()
        transferred = wintypes.DWORD()
        ctypes.set_last_error(0)
        started = self._api.kernel32.ReadFile(
            self._handle, buffer, size, ctypes.byref(transferred),
            ctypes.byref(overlapped),
        )
        count = self._settle(started, overlapped, deadline)
        return None if count is None else buffer.raw[:count]

    def write(self, payload: bytes, deadline: float) -> bool:
        overlapped = self._begin()
        transferred = wintypes.DWORD()
        ctypes.set_last_error(0)
        started = self._api.kernel32.WriteFile(
            self._handle, payload, len(payload), ctypes.byref(transferred),
            ctypes.byref(overlapped),
        )
        return self._settle(started, overlapped, deadline) == len(payload)

    def _begin(self) -> _Overlapped:
        self._api.kernel32.ResetEvent(self._io_event)
        overlapped = _Overlapped()
        overlapped.hEvent = self._io_event
        return overlapped

    def _settle(
        self, started: object, overlapped: _Overlapped, deadline: float
    ) -> int | None:
        """Return the bytes transferred, or None if the deadline or the stop won."""
        if not started:
            if ctypes.get_last_error() != _ERROR_IO_PENDING:
                return None
            if not self._wait(deadline):
                self._api.kernel32.CancelIoEx(self._handle, ctypes.byref(overlapped))
                # Drain the cancelled request so the OVERLAPPED can be released.
                self._collect(overlapped, wait=True)
                return None
        return self._collect(overlapped, wait=False)

    def _wait(self, deadline: float) -> bool:
        remaining = max(int((deadline - time.monotonic()) * 1000), 0)
        handles = (ctypes.c_void_p * 2)(self._io_event, self._stop_event)
        outcome = self._api.kernel32.WaitForMultipleObjects(
            2, handles, False, min(remaining, _MAX_WAIT_MILLISECONDS)
        )
        return outcome == _WAIT_OBJECT_0

    def _collect(self, overlapped: _Overlapped, *, wait: bool) -> int | None:
        transferred = wintypes.DWORD()
        if not self._api.kernel32.GetOverlappedResult(
            self._handle, ctypes.byref(overlapped), ctypes.byref(transferred), wait
        ):
            return None
        return transferred.value


class WindowsLocalControlPipe:
    """Serves one owner-only pipe instance on a background thread."""

    PIPE_ACCESS_DUPLEX = 0x00000003
    FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    PIPE_TYPE_BYTE = 0x00000000
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_WAIT = 0x00000000
    PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    PIPE_MODE = (
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS
    )
    #: One instance, so nothing else can serve this identity's pipe name.
    MAX_INSTANCES = 1
    BUFFER_BYTES = MAX_REQUEST_BYTES + 1
    MAX_CONSECUTIVE_FAILURES = 100

    def __init__(
        self,
        server: LocalControlServer,
        pipe_name: str,
        *,
        owner_sid: str,
        connection_timeout_seconds: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    ) -> None:
        if owner_sid != server.owner_sid:
            raise ValueError("the pipe ACL and the accepted peer must be one identity")
        if connection_timeout_seconds <= 0:
            raise ValueError("connection_timeout_seconds must be positive")
        self._server = server
        self.pipe_name = pipe_name
        self._descriptor = owner_only_security_descriptor(owner_sid)
        self._timeout = connection_timeout_seconds
        self._platform = WindowsMutexPlatform()
        self._api = _PipeApi()
        self._handle: object | None = None
        self._channel: _BoundedPipeChannel | None = None
        self._stop_event: object | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.consecutive_failures = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event = self._api.create_event()
        self._handle = self._create_instance()
        self._channel = _BoundedPipeChannel(self._api, self._handle, self._stop_event)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stopping.set()
        if self._stop_event is not None:
            self._api.kernel32.SetEvent(self._stop_event)
        if self._handle is not None:
            self._api.kernel32.CancelIoEx(self._handle, None)
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                # Never close a handle a live thread may still be waiting on.
                return
            self._thread = None
        if self._channel is not None:
            self._channel.close()
            self._channel = None
        handle, self._handle = self._handle, None
        self._api.close(handle)
        stop_event, self._stop_event = self._stop_event, None
        self._api.close(stop_event)

    def _create_instance(self) -> object:
        descriptor = ctypes.c_void_p()
        convert = self._api.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        if not convert(self._descriptor, _SDDL_REVISION_1, ctypes.byref(descriptor), None):
            _raise_last_error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            attributes = _SecurityAttributes()
            attributes.nLength = ctypes.sizeof(_SecurityAttributes)
            attributes.lpSecurityDescriptor = descriptor
            attributes.bInheritHandle = False
            handle = self._api.kernel32.CreateNamedPipeW(
                self.pipe_name,
                self.PIPE_ACCESS_DUPLEX
                | self.FILE_FLAG_FIRST_PIPE_INSTANCE
                | _FILE_FLAG_OVERLAPPED,
                self.PIPE_MODE,
                self.MAX_INSTANCES,
                self.BUFFER_BYTES,
                self.BUFFER_BYTES,
                0,
                ctypes.byref(attributes),
            )
            if not handle or handle == _INVALID_HANDLE_VALUE:
                _raise_last_error("CreateNamedPipeW")
            return handle
        finally:
            self._api.kernel32.LocalFree(descriptor)

    def _serve(self) -> None:
        channel = self._channel
        while not self._stopping.is_set():
            connected = False
            try:
                connected = channel.connect(time.monotonic() + self._timeout)
                if connected and not self._stopping.is_set():
                    self._serve_one(channel)
                self.consecutive_failures = 0
            except Exception:
                # Nothing about a failed caller is safe to log, so count instead:
                # a persistently failing pipe stops rather than spinning silently.
                self.consecutive_failures += 1
                if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    return
            finally:
                if connected:
                    self._api.kernel32.DisconnectNamedPipe(self._handle)

    def _serve_one(self, channel: _BoundedPipeChannel) -> None:
        deadline = time.monotonic() + self._timeout
        payload = self._read_request(channel, deadline)
        if payload is None:
            return
        # Impersonation only reports the real caller once its request has been read;
        # before that Windows hands back an anonymous token.
        peer_sid = self._peer_sid()
        response = self._server.handle_payload(payload, peer_sid)
        if not channel.write(response + b"\n", deadline):
            return
        # Let a well-behaved client read the answer and hang up before disconnecting,
        # which would otherwise discard whatever it had not yet read.
        channel.read(1, deadline)

    def _read_request(
        self, channel: _BoundedPipeChannel, deadline: float
    ) -> bytes | None:
        collected = bytearray()
        while b"\n" not in collected and len(collected) <= MAX_REQUEST_BYTES:
            chunk = channel.read(self.BUFFER_BYTES, deadline)
            if not chunk:
                break
            collected.extend(chunk)
        if not collected:
            return None
        return bytes(collected).split(b"\n", 1)[0]

    def _peer_sid(self) -> str | None:
        if not self._api.advapi32.ImpersonateNamedPipeClient(self._handle):
            return None
        try:
            access = ctypes.c_void_p()
            if not self._api.advapi32.OpenThreadToken(
                self._api.kernel32.GetCurrentThread(),
                _TOKEN_QUERY,
                True,
                ctypes.byref(access),
            ):
                return None
            try:
                return self._platform.sid_from_token(access)
            finally:
                self._api.close(access)
        except Exception:
            return None
        finally:
            if not self._api.advapi32.RevertToSelf():
                # This thread would keep running as the caller. Stop serving rather
                # than answer anything further under a borrowed identity.
                self._stopping.set()
                if self._stop_event is not None:
                    self._api.kernel32.SetEvent(self._stop_event)


class LocalControlClient:
    """Sends one typed command and reads one typed answer, both bounded."""

    def __init__(
        self,
        pipe_name: str,
        *,
        connect_timeout_seconds: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    ) -> None:
        self._pipe_name = pipe_name
        self._connect_timeout_seconds = connect_timeout_seconds
        self._api = _PipeApi()

    def status(self) -> LocalControlResponse:
        return self.send("STATUS")

    def graceful_shutdown(self) -> LocalControlResponse:
        return self.send("GRACEFUL_SHUTDOWN")

    def server_process_id(self) -> int:
        """The PID serving this pipe, so a caller can verify the exact process."""
        handle, stop_event = self._connect()
        try:
            value = wintypes.DWORD()
            if not self._api.kernel32.GetNamedPipeServerProcessId(
                handle, ctypes.byref(value)
            ):
                _raise_last_error("GetNamedPipeServerProcessId")
            return value.value
        finally:
            self._release(handle, stop_event)

    def send(self, command: str) -> LocalControlResponse:
        request = json.dumps({"command": command}, separators=(",", ":")).encode("utf-8")
        handle, stop_event = self._connect()
        channel = _BoundedPipeChannel(self._api, handle, stop_event)
        try:
            deadline = time.monotonic() + self._connect_timeout_seconds
            if not channel.write(request + b"\n", deadline):
                raise OSError("the Worker did not accept the control request")
            answer = channel.read(MAX_REQUEST_BYTES + 1, deadline)
        finally:
            channel.close()
            self._release(handle, stop_event)
        return _decode_answer(answer)

    def _connect(self) -> tuple[object, object]:
        # SECURITY_IDENTIFICATION lets a legitimate server read this caller's SID
        # while denying any impostor serving the same name the right to act as it.
        deadline = time.monotonic() + self._connect_timeout_seconds
        while True:
            ctypes.set_last_error(0)
            handle = self._api.kernel32.CreateFileW(
                self._pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OVERLAPPED
                | _SECURITY_SQOS_PRESENT
                | _SECURITY_IDENTIFICATION,
                None,
            )
            if handle and handle != _INVALID_HANDLE_VALUE:
                return handle, self._api.create_event()
            if time.monotonic() >= deadline:
                _raise_last_error("CreateFileW")
            time.sleep(0.02)

    def _release(self, handle: object, stop_event: object) -> None:
        self._api.close(handle)
        self._api.close(stop_event)


def _decode_answer(answer: bytes | None) -> LocalControlResponse:
    if not answer:
        raise OSError("the Worker returned no control answer")
    try:
        body = json.loads(answer.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise OSError("the Worker returned an unreadable control answer") from None
    if not isinstance(body, dict) or not isinstance(body.get("code"), str):
        raise OSError("the Worker returned an unreadable control answer")
    status = body.get("status")
    return LocalControlResponse(body["code"], status if isinstance(status, str) else None)


def _raise_last_error(operation: str) -> None:
    error = ctypes.get_last_error()
    raise OSError(error, f"{operation} failed")
