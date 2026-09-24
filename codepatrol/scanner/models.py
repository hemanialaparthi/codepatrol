"""Results produced by a project scan."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    rule_id: str
    message: str
    file: Path
    line: int
    column: int
    matched_source: str


@dataclass(frozen=True)
class ScanResult:
    violations: list[Violation]
    files_scanned: int
    policies_checked: int

