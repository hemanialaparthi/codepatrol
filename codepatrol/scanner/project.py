"""Discover relevant files and apply compiled structural policies."""

import fnmatch
import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codepatrol.languages.base import LanguageAdapter
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import CompiledPolicy
from codepatrol.scanner.matcher import find_matches, walk
from codepatrol.scanner.models import (
    CheckError,
    ScanResult,
    SourceLocation,
    Violation,
    normalize_report_path,
)

IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
}
IGNORED_SUFFIXES = (".egg-info",)


@dataclass
class _DiscoveryResult:
    files: list[Path] = field(default_factory=list)
    errors: list[CheckError] = field(default_factory=list)
    excluded_files: int = 0


class ProjectScanner:
    """Discover explicit inputs and structurally check every eligible file."""

    def __init__(self, registry: LanguageRegistry) -> None:
        self._registry = registry

    def scan(
        self,
        inputs: Path | Sequence[Path],
        policies: list[CompiledPolicy],
        exclusions: Iterable[str] = (),
    ) -> ScanResult:
        input_paths = [inputs] if isinstance(inputs, Path) else list(inputs)
        root = _report_root(input_paths)
        policies_by_extension: dict[str, list[tuple[LanguageAdapter, CompiledPolicy]]] = (
            defaultdict(list)
        )
        extensions: set[str] = set()
        for policy in policies:
            adapter = self._registry.get(policy.policy.language)
            for extension in adapter.extensions:
                extensions.add(extension)
                policies_by_extension[extension].append((adapter, policy))

        discovery = self._discover_files(
            input_paths, root, tuple(sorted(extensions)), tuple(exclusions)
        )
        result = ScanResult(
            errors=discovery.errors,
            policies_checked=len(policies),
            files_selected=len(discovery.files),
            files_excluded=discovery.excluded_files,
            root=root,
        )

        for source_file in discovery.files:
            file_policies = policies_by_extension[source_file.suffix]
            adapter = file_policies[0][0]
            compiled = [item[1] for item in file_policies]
            violations, errors = self._scan_file(source_file, adapter, compiled)
            result.violations.extend(violations)
            result.errors.extend(errors)
            if not errors:
                result.files_scanned += 1

        if not discovery.files:
            result.errors.append(
                CheckError(
                    category="discovery",
                    message="No eligible source files were selected.",
                )
            )
        result.sort()
        return result

    def _discover_files(
        self,
        inputs: list[Path],
        root: Path,
        extensions: tuple[str, ...],
        exclusions: tuple[str, ...],
    ) -> _DiscoveryResult:
        result = _DiscoveryResult()
        discovered: dict[Path, Path] = {}
        excluded: set[Path] = set()

        for supplied_path in inputs:
            path = supplied_path.expanduser()
            if not path.exists():
                result.errors.append(
                    CheckError(
                        category="discovery",
                        message=f"Input path does not exist: '{path}'.",
                        file=path,
                    )
                )
                continue
            if path.is_symlink() and path.is_dir():
                result.errors.append(
                    CheckError(
                        category="discovery",
                        message=f"Directory symlinks are not traversed: '{path}'.",
                        file=path,
                    )
                )
                continue
            if path.is_file():
                if path.suffix not in extensions:
                    result.errors.append(
                        CheckError(
                            category="discovery",
                            message=f"Unsupported explicitly supplied file: '{path}'.",
                            file=path,
                        )
                    )
                    continue
                if _is_excluded(path, root, exclusions):
                    excluded.add(path.resolve(strict=False))
                    continue
                self._add_discovered(path, discovered, result)
                continue
            if not path.is_dir():
                result.errors.append(
                    CheckError(
                        category="discovery",
                        message=f"Input is not a regular file or directory: '{path}'.",
                        file=path,
                    )
                )
                continue

            discovery_errors: list[OSError] = []

            def record_walk_error(error: OSError) -> None:
                discovery_errors.append(error)

            for directory, child_directories, filenames in os.walk(
                path, topdown=True, onerror=record_walk_error, followlinks=False
            ):
                directory_path = Path(directory)
                retained_directories: list[str] = []
                for name in sorted(child_directories):
                    child = directory_path / name
                    if child.is_symlink() or _is_excluded(child, root, exclusions):
                        excluded.update(_eligible_files_under(child, extensions))
                    else:
                        retained_directories.append(name)
                child_directories[:] = retained_directories

                for filename in sorted(filenames):
                    candidate = directory_path / filename
                    if candidate.suffix not in extensions:
                        continue
                    if _is_excluded(candidate, root, exclusions):
                        excluded.add(candidate.resolve(strict=False))
                        continue
                    self._add_discovered(candidate, discovered, result)

            for error in discovery_errors:
                error_path = Path(error.filename) if error.filename else path
                result.errors.append(
                    CheckError(
                        category="discovery",
                        message=f"Could not inspect '{error_path}': {error.strerror or error}.",
                        file=error_path,
                    )
                )

        result.files = sorted(
            discovered.values(), key=lambda item: normalize_report_path(item, root)
        )
        result.excluded_files = len(excluded - set(discovered))
        return result

    @staticmethod
    def _add_discovered(
        path: Path, discovered: dict[Path, Path], result: _DiscoveryResult
    ) -> None:
        try:
            canonical = path.resolve(strict=True)
        except OSError as exc:
            result.errors.append(
                CheckError(
                    category="discovery",
                    message=f"Could not resolve '{path}': {exc}.",
                    file=path,
                )
            )
            return
        discovered.setdefault(canonical, canonical)

    def _scan_file(
        self,
        source_file: Path,
        adapter: LanguageAdapter,
        policies: list[CompiledPolicy],
    ) -> tuple[list[Violation], list[CheckError]]:
        try:
            source = source_file.read_bytes()
        except OSError as exc:
            return [], [
                CheckError(
                    category="reading",
                    message=f"Could not read '{source_file}': {exc}.",
                    file=source_file,
                )
            ]

        try:
            source.decode("utf-8")
        except UnicodeDecodeError as exc:
            location = _byte_offset_location(source, exc.start)
            return [], [
                CheckError(
                    category="decoding",
                    message=f"Could not decode '{source_file}' as UTF-8: {exc}.",
                    file=source_file,
                    start=location,
                    end=location,
                )
            ]

        try:
            tree = adapter.parse_source(source)
        except Exception as exc:
            return [], [
                CheckError(
                    category="checking",
                    message=f"Parser failed for '{source_file}': {exc}.",
                    file=source_file,
                )
            ]

        parse_errors = _parse_errors(tree.root_node, source_file, source)
        if parse_errors:
            return [], parse_errors

        violations: list[Violation] = []
        seen: set[tuple[Any, ...]] = set()
        for compiled_policy in policies:
            try:
                matches = find_matches(compiled_policy.pattern, tree.root_node, source)
                for node in matches:
                    start = _point_location(source, node.start_point)
                    end = _point_location(source, node.end_point)
                    key = (
                        compiled_policy.policy.id,
                        node.start_byte,
                        node.end_byte,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    violations.append(
                        Violation(
                            rule_id=compiled_policy.policy.id,
                            message=compiled_policy.policy.message,
                            file=source_file,
                            line=start.line,
                            column=start.column,
                            end_line=end.line,
                            end_column=end.column,
                            matched_source=source[node.start_byte : node.end_byte].decode(
                                "utf-8"
                            ),
                        )
                    )
            except Exception as exc:
                return [], [
                    CheckError(
                        category="checking",
                        message=(
                            f"Rule '{compiled_policy.policy.id}' failed while checking "
                            f"'{source_file}': {exc}."
                        ),
                        file=source_file,
                        policy_id=compiled_policy.policy.id,
                    )
                ]
        return violations, []


def _report_root(inputs: list[Path]) -> Path:
    if not inputs:
        return Path.cwd().resolve()
    resolved = [path.expanduser().resolve(strict=False) for path in inputs]
    if len(resolved) == 1:
        only = resolved[0]
        return only if only.is_dir() else only.parent
    bases = [path if path.is_dir() else path.parent for path in resolved]
    try:
        return Path(os.path.commonpath([str(path) for path in bases]))
    except ValueError:
        return Path.cwd().resolve()


def _is_excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    if any(part in IGNORED_DIRECTORIES for part in path.parts) or any(
        part.endswith(IGNORED_SUFFIXES) for part in path.parts
    ):
        return True
    try:
        relative = path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        relative = path.resolve(strict=False).as_posix()
    for pattern in patterns:
        normalized = pattern.replace(os.sep, "/").rstrip("/")
        if not normalized:
            continue
        if fnmatch.fnmatchcase(relative, normalized) or fnmatch.fnmatchcase(
            path.name, normalized
        ):
            return True
        if relative == normalized or relative.startswith(normalized + "/"):
            return True
    return False


def _eligible_files_under(path: Path, extensions: tuple[str, ...]) -> set[Path]:
    if path.is_symlink():
        return set()
    if path.is_file():
        return {path.resolve(strict=False)} if path.suffix in extensions else set()
    files: set[Path] = set()
    try:
        for directory, directories, filenames in os.walk(path, followlinks=False):
            directory_path = Path(directory)
            directories[:] = [
                name for name in directories if not (directory_path / name).is_symlink()
            ]
            files.update(
                (directory_path / name).resolve(strict=False)
                for name in filenames
                if Path(name).suffix in extensions
            )
    except OSError:
        return files
    return files


def _parse_errors(root: Any, source_file: Path, source: bytes) -> list[CheckError]:
    errors: list[CheckError] = []
    for node in walk(root):
        if not (node.is_error or node.is_missing):
            continue
        start = _point_location(source, node.start_point)
        end = _point_location(source, node.end_point)
        description = "missing syntax" if node.is_missing else "syntax error"
        errors.append(
            CheckError(
                category="parsing",
                message=(
                    f"Tree-sitter found {description} in '{source_file}' at "
                    f"{start.line}:{start.column}."
                ),
                file=source_file,
                start=start,
                end=end,
            )
        )
    if root.has_error and not errors:
        errors.append(
            CheckError(
                category="parsing",
                message=f"Tree-sitter found an unlocated syntax error in '{source_file}'.",
                file=source_file,
            )
        )
    return errors


def _point_location(source: bytes, point: Any) -> SourceLocation:
    """Convert Tree-sitter's zero-based byte column to a character column."""
    lines = source.splitlines(keepends=True)
    line_bytes = lines[point.row] if point.row < len(lines) else b""
    prefix = line_bytes[: point.column]
    return SourceLocation(point.row + 1, len(prefix.decode("utf-8")) + 1)


def _byte_offset_location(source: bytes, offset: int) -> SourceLocation:
    prefix = source[:offset]
    line = prefix.count(b"\n") + 1
    line_prefix = prefix.rsplit(b"\n", 1)[-1]
    return SourceLocation(line, len(line_prefix.decode("utf-8", errors="replace")) + 1)
