"""Bounded, framework-independent subprocess execution."""

import math
import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from codepatrol.testing.models import ExecutionError, TestCommandResult

DEFAULT_PREVIEW_BYTES = 64 * 1024
TERMINATION_GRACE_SECONDS = 2.0


def parse_command(command: str) -> list[str]:
    """Split a command using portable POSIX-style quoting, without a shell."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("The test command must not be empty.")
    try:
        arguments = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"Invalid test command quoting: {exc}.") from exc
    if not arguments:
        raise ValueError("The test command must contain an executable.")
    if "\x00" in command:
        raise ValueError("The test command must not contain NUL bytes.")
    return arguments


def run_test_command(
    command: str,
    *,
    working_directory: Path,
    timeout_seconds: float = 60.0,
    artifact_root: Path | None = None,
    preview_bytes: int = DEFAULT_PREVIEW_BYTES,
) -> TestCommandResult:
    """Execute exactly ``command`` with ``shell=False`` and capture log artifacts."""
    started = time.monotonic()
    cwd = working_directory.expanduser().resolve(strict=False)
    validation_error = _validate_inputs(command, cwd, timeout_seconds, preview_bytes)
    if validation_error is not None:
        return _error_result(cwd, started, validation_error)

    try:
        arguments = parse_command(command)
    except ValueError as exc:
        return _error_result(
            cwd,
            started,
            ExecutionError("command", str(exc), type(exc).__name__),
        )

    logs_root = (
        artifact_root.expanduser().resolve(strict=False)
        if artifact_root is not None
        else cwd / ".codepatrol" / "artifacts"
    )
    try:
        logs_root.mkdir(parents=True, exist_ok=True)
        run_directory = Path(tempfile.mkdtemp(prefix="run-", dir=logs_root))
        stdout_path = run_directory / "stdout.log"
        stderr_path = run_directory / "stderr.log"
        stdout_handle = stdout_path.open("wb")
        try:
            stderr_handle = stderr_path.open("wb")
        except BaseException:
            stdout_handle.close()
            raise
    except OSError as exc:
        return _error_result(
            cwd,
            started,
            ExecutionError(
                "artifact",
                f"Could not create test log artifacts under '{logs_root}': {exc}",
                type(exc).__name__,
            ),
            arguments=arguments,
        )

    process: subprocess.Popen[bytes] | None = None
    outcome = "ERROR"
    execution_errors: list[ExecutionError] = []
    try:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            shell=False,
            start_new_session=os.name == "posix",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
        try:
            deadline = time.monotonic() + timeout_seconds
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
            outcome = "PASS" if process.returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            outcome = "TIMEOUT"
            if not _terminate_process_tree(process):
                execution_errors.append(
                    ExecutionError(
                        "process_control",
                        "The timed-out test process could not be confirmed terminated.",
                    )
                )
        except KeyboardInterrupt:
            outcome = "INTERRUPTED"
            if not _terminate_process_tree(process):
                execution_errors.append(
                    ExecutionError(
                        "process_control",
                        "The interrupted test process could not be confirmed terminated.",
                    )
                )
        except OSError as exc:
            execution_errors.append(
                ExecutionError(
                    "process_control",
                    f"Could not wait for the supplied test command reliably: {exc}",
                    type(exc).__name__,
                )
            )
            _terminate_process_tree(process)
    except OSError as exc:
        execution_errors.append(
            ExecutionError(
                "launch",
                f"Could not start the supplied test command: {exc}",
                type(exc).__name__,
            )
        )
    finally:
        for stream_name, handle in (
            ("stdout", stdout_handle),
            ("stderr", stderr_handle),
        ):
            try:
                # The child writes through the same open file description.
                # fsync exposes delayed storage failures before we trust a log.
                handle.flush()
                os.fsync(handle.fileno())
            except OSError as exc:
                execution_errors.append(
                    ExecutionError(
                        "artifact",
                        f"Could not finish the {stream_name} log reliably: {exc}",
                        type(exc).__name__,
                    )
                )
            finally:
                try:
                    handle.close()
                except OSError as exc:
                    execution_errors.append(
                        ExecutionError(
                            "artifact",
                            f"Could not close the {stream_name} log reliably: {exc}",
                            type(exc).__name__,
                        )
                    )

    duration = time.monotonic() - started
    try:
        stdout_preview, stdout_truncated = _read_preview(stdout_path, preview_bytes)
        stderr_preview, stderr_truncated = _read_preview(stderr_path, preview_bytes)
    except OSError as exc:
        execution_errors.append(
            ExecutionError(
                "capture",
                f"Could not read captured test output reliably: {exc}",
                type(exc).__name__,
            )
        )
        outcome = "ERROR"
        stdout_preview = stderr_preview = ""
        stdout_truncated = stderr_truncated = False

    if execution_errors:
        outcome = "ERROR"
    return TestCommandResult(
        outcome=outcome,  # type: ignore[arg-type]
        arguments=arguments,
        working_directory=cwd,
        exit_code=process.returncode if process is not None else None,
        duration_seconds=duration,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        stdout_log=stdout_path,
        stderr_log=stderr_path,
        errors=execution_errors,
    )


def _validate_inputs(
    command: str, cwd: Path, timeout_seconds: float, preview_bytes: int
) -> ExecutionError | None:
    try:
        parse_command(command)
    except ValueError as exc:
        return ExecutionError("command", str(exc), type(exc).__name__)
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        return ExecutionError("timeout", "The test timeout must be greater than zero.")
    if preview_bytes < 0:
        return ExecutionError("capture", "The preview byte limit must not be negative.")
    if not cwd.exists():
        return ExecutionError(
            "working_directory", f"Test working directory does not exist: '{cwd}'."
        )
    if not cwd.is_dir():
        return ExecutionError(
            "working_directory", f"Test working directory is not a directory: '{cwd}'."
        )
    return None


def _error_result(
    cwd: Path,
    started: float,
    error: ExecutionError,
    *,
    arguments: list[str] | None = None,
) -> TestCommandResult:
    return TestCommandResult(
        outcome="ERROR",
        arguments=arguments or [],
        working_directory=cwd,
        exit_code=None,
        duration_seconds=time.monotonic() - started,
        errors=[error],
    )


def _read_preview(path: Path, limit: int) -> tuple[str, bool]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        content = handle.read(limit)
    return content.decode("utf-8", errors="replace"), size > len(content)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> bool:
    """Best-effort termination of a process group, followed by forced cleanup."""
    if process.poll() is not None:
        return True
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
        return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.poll() is not None
