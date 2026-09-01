"""Domain errors that produce clear worker startup failures."""


class WorkerError(RuntimeError):
    """Base class for expected Worker failures."""


class DependencyError(WorkerError):
    """A required runtime dependency is unavailable."""


class CudaUnavailableError(WorkerError):
    """PyTorch cannot use a CUDA device."""


class GpuInspectionError(WorkerError):
    """NVML could not inspect the selected GPU."""


class DeviceVerificationError(WorkerError):
    """The embedding model did not remain on the requested device."""


class WorkerConfigurationError(WorkerError):
    """The STEP 2 Worker client configuration is invalid."""


class WorkerTransportError(WorkerError):
    """The STEP 2 Backend WebSocket connection failed."""


class WorkerProtocolError(WorkerError):
    """A Backend message violates the fixed Worker protocol."""


class DockerManagerError(WorkerError):
    """A Docker readiness or execution operation failed."""

    def __init__(self, code: str, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class SetupRequiredError(WorkerError):
    """Local prerequisites are missing; the Worker must not register."""


class WorkerAlreadyRunningError(WorkerError):
    """Another Worker process already owns this user/Worker ID/GPU identity."""


class CredentialMismatchError(WorkerError):
    """The Backend rejected the Worker ID and Token pair during REGISTER."""


class LocalControlError(WorkerError):
    """The current-user local control interface could not be served."""


class BenchmarkError(WorkerError):
    """The mandatory startup benchmark could not produce a trusted result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

