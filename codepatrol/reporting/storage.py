"""Protected, atomic JSON report persistence."""

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

PROJECT_CONFIGURATION_NAMES = {
    ".coveragerc",
    ".editorconfig",
    ".gitignore",
    ".pre-commit-config.yaml",
    "Cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "tox.ini",
}


def validate_report_destination(
    destination: Path,
    *,
    source_inputs: Iterable[Path],
    policy_file: Path,
    command_arguments: Iterable[str] = (),
    command_working_directory: Path | None = None,
) -> Path:
    """Resolve a report path and reject known source, test, and config targets."""
    resolved = destination.expanduser().resolve(strict=False)
    protected_files = {policy_file.expanduser().resolve(strict=False)}
    protected_directories: set[Path] = set()

    for supplied in source_inputs:
        candidate = supplied.expanduser().resolve(strict=False)
        if candidate.is_file():
            protected_files.add(candidate)
        elif candidate.is_dir():
            protected_directories.add(candidate)

    command_cwd = (command_working_directory or Path.cwd()).resolve(strict=False)
    for argument in command_arguments:
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            candidate = command_cwd / candidate
        candidate = candidate.resolve(strict=False)
        if candidate.is_file():
            protected_files.add(candidate)
        elif candidate.is_dir() and candidate.name.lower() in {"test", "tests"}:
            protected_directories.add(candidate)

    if resolved in protected_files:
        raise ValueError(f"Report destination is a protected input file: '{resolved}'.")
    if resolved.exists() and resolved.name in PROJECT_CONFIGURATION_NAMES:
        raise ValueError(
            f"Report destination is a protected project configuration file: '{resolved}'."
        )
    if resolved.exists():
        for directory in protected_directories:
            try:
                relative = resolved.relative_to(directory)
            except ValueError:
                continue
            if resolved.suffix == ".py" or "tests" in {
                part.lower() for part in relative.parts
            } or directory.name.lower() in {"test", "tests"}:
                raise ValueError(
                    f"Report destination would overwrite source or test input: '{resolved}'."
                )
    return resolved


def save_report_atomic(serialized_report: str, destination: Path) -> None:
    """Create parent directories and atomically replace a report file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(serialized_report)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
