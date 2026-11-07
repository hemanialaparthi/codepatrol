"""Bounded project-root staging for candidate validation."""

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tempfile

from codepatrol.errors import CodePatrolError

STAGING_EXCLUDED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".tox", ".nox",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist",
}
PROJECT_MARKERS = ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini")


class StagingError(CodePatrolError):
    """The project or command cannot be staged safely."""


@dataclass
class StagedProject:
    root: Path
    source_root: Path
    _temporary: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self._temporary.cleanup()


def infer_project_root(paths: list[Path], supplied: Path | None) -> Path:
    if supplied is not None:
        root = supplied.expanduser().resolve(strict=False)
    elif len(paths) == 1 and paths[0].expanduser().resolve(strict=False).is_dir():
        root = paths[0].expanduser().resolve(strict=True)
    elif len(paths) == 1:
        current = paths[0].expanduser().resolve(strict=False).parent
        root = _nearest_marker_root(current)
        if root is None:
            raise StagingError(
                "Fix mode needs --project-root for a file input without a recognized "
                "project marker (pyproject.toml, setup.cfg, tox.ini, or pytest.ini)."
            )
    else:
        raise StagingError("Fix mode needs --project-root for multiple input paths.")
    if not root.exists() or not root.is_dir():
        raise StagingError(f"Project root is not a directory: '{root}'.")
    return root


def _nearest_marker_root(start: Path) -> Path | None:
    current = start
    while True:
        if any((current / marker).is_file() for marker in PROJECT_MARKERS):
            return current
        if current.parent == current:
            return None
        current = current.parent


def validate_scope(root: Path, paths: list[Path], test_cwd: Path) -> list[Path]:
    resolved_root = root.resolve(strict=True)
    targets: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise StagingError(f"Repair target is outside project root: '{path}'.") from exc
        targets.append(resolved)
    try:
        test_cwd.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise StagingError(f"Test working directory is outside project root: '{test_cwd}'.") from exc
    for directory, names, filenames in os.walk(resolved_root, followlinks=False):
        for name in [*names, *filenames]:
            candidate = Path(directory) / name
            if candidate.is_symlink():
                raise StagingError(
                    f"Fix staging refuses symlinks in the project root: '{candidate}'."
                )
    return targets


def create_stage(root: Path) -> StagedProject:
    temporary = tempfile.TemporaryDirectory(prefix="codepatrol-stage-")
    destination = Path(temporary.name) / root.name
    try:
        shutil.copytree(
            root,
            destination,
            ignore=shutil.ignore_patterns(*STAGING_EXCLUDED_DIRECTORIES),
            symlinks=False,
        )
    except (OSError, shutil.Error) as exc:
        temporary.cleanup()
        raise StagingError(f"Could not create staged project: {exc}") from exc
    return StagedProject(root=destination, source_root=destination, _temporary=temporary)


def map_into_stage(path: Path, original_root: Path, stage_root: Path) -> Path:
    try:
        relative = path.resolve(strict=False).relative_to(original_root.resolve(strict=True))
    except ValueError as exc:
        raise StagingError(f"Path cannot be mapped into staged project: '{path}'.") from exc
    return stage_root / relative


def validate_test_command(arguments: list[str], project_root: Path) -> None:
    if not arguments:
        raise StagingError("The staged test command has no executable.")
    executable = Path(arguments[0]).expanduser()
    if not executable.is_absolute() and (
        executable.name != arguments[0] or arguments[0].startswith(".")
    ):
        raise StagingError(
            "Staged test commands must use a bare executable name or an existing "
            "virtual-environment executable; project scripts are unsupported."
        )
    if executable.is_absolute():
        try:
            executable.resolve(strict=False).relative_to(project_root.resolve(strict=True))
        except ValueError:
            pass
        else:
            raise StagingError(
                "Staged test commands may use an existing virtual-environment executable, "
                "but not an executable stored inside the project root."
            )
    for argument in arguments[1:]:
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            continue
        try:
            candidate.resolve(strict=False).relative_to(project_root.resolve(strict=True))
        except ValueError:
            continue
        raise StagingError(
            "Staged test commands must use relative source and test paths; "
            f"absolute project path '{argument}' is unsupported."
        )
