"""Shared atomic no-replace directory publication primitives."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from pathlib import Path

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_WINDOWS_ACCESS_DENIED = 5
_WINDOWS_PUBLICATION_ATTEMPTS = 3


class _PublicationFailureDiagnostic(RuntimeError):
    """Path-free internal evidence for a failed atomic publication."""

    def __init__(
        self,
        *,
        error: OSError,
        source_exists: bool | None,
        destination_exists: bool | None,
        temporary_entries: tuple[str, ...],
    ) -> None:
        self.exception_type = type(error).__name__
        self.errno = error.errno
        self.winerror = getattr(error, "winerror", None)
        self.source_exists = source_exists
        self.destination_exists = destination_exists
        self.temporary_entries = temporary_entries
        self.writers_closed = True
        super().__init__(
            "Atomic publication failed "
            f"type={self.exception_type} errno={self.errno} "
            f"winerror={self.winerror} source_exists={source_exists} "
            f"destination_exists={destination_exists} "
            f"temporary_entries={temporary_entries!r} writers_closed=True."
        )


def _publication_failure_diagnostic(
    error: OSError,
    source: Path,
    destination: Path,
) -> _PublicationFailureDiagnostic:
    source_exists, temporary_entries = _safe_directory_state(source)
    destination_exists, _ = _safe_directory_state(destination)
    return _PublicationFailureDiagnostic(
        error=error,
        source_exists=source_exists,
        destination_exists=destination_exists,
        temporary_entries=temporary_entries,
    )


def _safe_directory_state(path: Path) -> tuple[bool | None, tuple[str, ...]]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, ()
    except OSError:
        return None, ()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return True, ()
    try:
        entries = tuple(sorted(item.name for item in path.iterdir())[:16])
    except OSError:
        entries = ()
    return True, entries


def _windows_rename(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _validate_windows_retry_state(source: Path, destination: Path) -> None:
    try:
        source_metadata = source.lstat()
    except OSError:
        raise
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISDIR(
        source_metadata.st_mode
    ):
        raise OSError(errno.EACCES, "Atomic publication source is unsafe.")
    try:
        destination.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise
    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename a directory while refusing an existing destination."""

    if sys.platform == "win32":
        for attempt in range(_WINDOWS_PUBLICATION_ATTEMPTS):
            try:
                _windows_rename(source, destination)
                return
            except OSError as error:
                if (
                    getattr(error, "winerror", None) != _WINDOWS_ACCESS_DENIED
                    or attempt + 1 >= _WINDOWS_PUBLICATION_ATTEMPTS
                ):
                    raise
                _validate_windows_retry_state(source, destination)
        raise AssertionError("Unreachable Windows publication state.")
    if not sys.platform.startswith("linux"):
        raise OSError(
            errno.ENOSYS,
            "Atomic no-replace directory publication is unavailable.",
        )
    try:
        standard_library = ctypes.CDLL(None, use_errno=True)
        renameat2 = standard_library.renameat2
    except (AttributeError, OSError):
        raise OSError(
            errno.ENOSYS,
            "Atomic no-replace directory publication is unavailable.",
        ) from None

    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    try:
        encoded_source = os.fsencode(source)
        encoded_destination = os.fsencode(destination)
    except (TypeError, UnicodeError):
        raise OSError(
            errno.EINVAL,
            "Atomic no-replace directory publication is unavailable.",
        ) from None

    ctypes.set_errno(0)
    result = renameat2(
        _AT_FDCWD,
        encoded_source,
        _AT_FDCWD,
        encoded_destination,
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno() or errno.EIO
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number))
    raise OSError(error_number, os.strerror(error_number))


__all__ = [
    "_PublicationFailureDiagnostic",
    "_publication_failure_diagnostic",
    "_rename_directory_no_replace",
]
