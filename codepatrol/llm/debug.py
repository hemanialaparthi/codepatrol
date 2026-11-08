"""Explicitly requested local storage for raw provider response content."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from codepatrol.reporting.storage import PROJECT_CONFIGURATION_NAMES


class DebugArtifactError(Exception):
    """A requested response artifact could not be safely written."""


def validate_debug_destination(
    destination: Path,
    *,
    source_inputs: list[Path],
    policy_file: Path,
    normal_report: Path | None,
    test_arguments: list[str],
    test_cwd: Path | None,
) -> Path:
    resolved = destination.expanduser().resolve(strict=False)
    protected = {policy_file.expanduser().resolve(strict=False)}
    source_directories: list[Path] = []
    for path in source_inputs:
        resolved_input = path.expanduser().resolve(strict=False)
        if path.is_file():
            protected.add(resolved_input)
        elif path.is_dir():
            source_directories.append(resolved_input)
    if normal_report is not None:
        protected.add(normal_report.expanduser().resolve(strict=False))
    if resolved in protected:
        raise DebugArtifactError(
            f"Debug response artifact would overwrite a protected input: '{resolved}'."
        )
    for source_directory in source_directories:
        try:
            relative = resolved.relative_to(source_directory)
        except ValueError:
            continue
        if resolved.suffix == ".py" or "tests" in {part.lower() for part in relative.parts}:
            raise DebugArtifactError(
                f"Debug response artifact would overwrite source or tests: '{resolved}'."
            )
    if resolved.name in PROJECT_CONFIGURATION_NAMES:
        raise DebugArtifactError(
            f"Debug response artifact cannot overwrite project configuration: '{resolved}'."
        )
    cwd = (test_cwd or Path.cwd()).expanduser().resolve(strict=False)
    for argument in test_arguments:
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        candidate = candidate.resolve(strict=False)
        if candidate == resolved:
            raise DebugArtifactError(
                f"Debug response artifact would overwrite a test input: '{resolved}'."
            )
        if candidate.is_dir():
            try:
                resolved.relative_to(candidate)
            except ValueError:
                pass
            else:
                raise DebugArtifactError(
                    f"Debug response artifact would overwrite a test directory: '{resolved}'."
                )
        if "tests" in {part.lower() for part in resolved.parts}:
            raise DebugArtifactError(
                f"Debug response artifact would overwrite a test path: '{resolved}'."
            )
    if resolved.exists() and not resolved.is_file():
        raise DebugArtifactError(
            f"Debug response artifact is not a regular file: '{resolved}'."
        )
    return resolved


def write_debug_response(
    destination: Path,
    *,
    model: str,
    endpoint_id: str,
    finish_reason: str | None,
    usage: dict[str, int],
    content: str | None,
    content_kind: str,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": "llm-debug-response-1.0",
        "model": model,
        "endpoint_id": endpoint_id,
        "finish_reason": finish_reason,
        "usage": usage,
        "content_kind": content_kind,
        "content": content,
        "warning": "Response content may contain sensitive project text.",
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = -1
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise DebugArtifactError(
            f"Could not write requested LLM response artifact '{destination}': {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
