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

