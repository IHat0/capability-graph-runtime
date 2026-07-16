"""Domain errors for the isolated trusted quantum-preflight runtime."""


class QuantumPreflightError(RuntimeError):
    """Base class for failures with a stable CLI category."""

    exit_code = 3


class QuantumManifestError(QuantumPreflightError):
    """The declared scientific identity is invalid."""

    exit_code = 2


class QuantumExecutionError(QuantumPreflightError):
    """The trusted scientific calculation did not complete."""

    exit_code = 3


class QuantumVerificationError(QuantumPreflightError):
    """Execution completed but scientific verification failed."""

    exit_code = 4


class QuantumIntegrityError(QuantumPreflightError):
    """Artifact identity or lineage validation failed."""

    exit_code = 5


class QuantumDependencyError(QuantumPreflightError):
    """The dedicated scientific dependency set is unavailable or incompatible."""

    exit_code = 6


class QuantumTimeoutError(QuantumPreflightError):
    """The bounded trusted run exceeded its wall-clock limit."""

    exit_code = 7
