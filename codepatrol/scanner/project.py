"""Discover relevant files and apply compiled structural policies."""

import os
from collections import defaultdict
from pathlib import Path

from codepatrol.errors import ScanError
from codepatrol.languages.base import LanguageAdapter
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import CompiledPolicy
from codepatrol.scanner.matcher import find_matches
from codepatrol.scanner.models import ScanResult, Violation

IGNORED_DIRECTORIES = {".git", ".venv", "venv", "__pycache__", "node_modules"}


class ProjectScanner:
    def __init__(self, registry: LanguageRegistry) -> None:
        self._registry = registry

    def scan(
        self, project: Path, policies: list[CompiledPolicy]
    ) -> ScanResult:
        policies_by_language: dict[str, list[CompiledPolicy]] = defaultdict(list)
        for policy in policies:
            policies_by_language[policy.policy.language].append(policy)

        violations: list[Violation] = []
        scanned_files: set[Path] = set()
        for language, language_policies in policies_by_language.items():
            adapter = self._registry.get(language)
            for source_file in self._discover_files(project, adapter.extensions):
                scanned_files.add(source_file)
                violations.extend(
                    self._scan_file(source_file, adapter, language_policies)
                )

        violations.sort(key=lambda item: (str(item.file), item.line, item.column, item.rule_id))
        return ScanResult(
            violations=violations,
            files_scanned=len(scanned_files),
            policies_checked=len(policies),
        )

    def _discover_files(
        self, project: Path, extensions: tuple[str, ...]
    ) -> list[Path]:
        discovered: list[Path] = []
        for directory, child_directories, filenames in os.walk(project):
            child_directories[:] = sorted(
                name for name in child_directories if name not in IGNORED_DIRECTORIES
            )
            for filename in sorted(filenames):
                path = Path(directory) / filename
                if path.suffix in extensions:
                    discovered.append(path)
        return discovered

    def _scan_file(
        self,
        source_file: Path,
        adapter: LanguageAdapter,
        policies: list[CompiledPolicy],
    ) -> list[Violation]:
        try:
            source = source_file.read_bytes()
        except OSError as exc:
            raise ScanError(f"Could not read '{source_file}': {exc}") from exc

        tree = adapter.parse_source(source)
        if tree.root_node.has_error:
            raise ScanError(f"Could not parse '{source_file}'.")

        violations: list[Violation] = []
        for compiled_policy in policies:
            for node in find_matches(
                compiled_policy.pattern, tree.root_node, source
            ):
                violations.append(
                    Violation(
                        rule_id=compiled_policy.policy.id,
                        message=compiled_policy.policy.message,
                        file=source_file,
                        line=node.start_point.row + 1,
                        column=node.start_point.column + 1,
                        matched_source=source[node.start_byte : node.end_byte].decode(
                            "utf-8", errors="replace"
                        ),
                    )
                )
        return violations
