"""Deterministic data models for findings, errors, and scan reports."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Status = Literal["PASS", "FAIL", "ERROR"]


@dataclass(frozen=True, order=True)
class SourceLocation:
    """A 1-based source position; end locations are exclusive."""

    line: int
    column: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column}


@dataclass(frozen=True)
class Violation:
    """One structural policy match in a source file."""

    rule_id: str
    message: str
    file: Path
    line: int
    column: int
    end_line: int
    end_column: int
    matched_source: str

    @property
    def start(self) -> SourceLocation:
        return SourceLocation(self.line, self.column)

    @property
    def end(self) -> SourceLocation:
        return SourceLocation(self.end_line, self.end_column)

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "file": normalize_report_path(self.file, root),
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "matched_source": self.matched_source,
        }


@dataclass(frozen=True)
class CheckError:
    """An expected failure that makes a scan incomplete or unreliable."""

    category: str
    message: str
    file: Path | None = None
    policy_id: str | None = None
    start: SourceLocation | None = None
    end: SourceLocation | None = None

    def to_dict(self, root: Path) -> dict[str, Any]:
        result: dict[str, Any] = {
            "category": self.category,
            "message": self.message,
        }
        if self.file is not None:
            result["file"] = normalize_report_path(self.file, root)
        if self.policy_id is not None:
            result["policy_id"] = self.policy_id
        if self.start is not None:
            result["start"] = self.start.to_dict()
        if self.end is not None:
            result["end"] = self.end.to_dict()
        return result


@dataclass
class ScanResult:
    """The complete result of discovery and checking."""

    violations: list[Violation] = field(default_factory=list)
    errors: list[CheckError] = field(default_factory=list)
    files_scanned: int = 0
    policies_checked: int = 0
    files_selected: int = 0
    files_excluded: int = 0
    root: Path = field(default_factory=Path.cwd)

    @property
    def findings(self) -> list[Violation]:
        """Preferred name for violations, retaining the original API name."""
        return self.violations

    @property
    def status(self) -> Status:
        if self.errors:
            return "ERROR"
        if self.violations:
            return "FAIL"
        return "PASS"

    def sort(self) -> None:
        """Put findings and errors into their documented deterministic order."""
        self.violations.sort(
            key=lambda item: (
                normalize_report_path(item.file, self.root),
                item.line,
                item.column,
                item.end_line,
                item.end_column,
                item.rule_id,
            )
        )
        self.errors.sort(
            key=lambda item: (
                item.category,
                normalize_report_path(item.file, self.root) if item.file else "",
                item.start.line if item.start else 0,
                item.start.column if item.start else 0,
                item.policy_id or "",
                item.message,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        self.sort()
        return {
            "schema_version": "1.0",
            "status": self.status,
            "summary": {
                "files": {
                    "selected": self.files_selected,
                    "scanned": self.files_scanned,
                    "excluded": self.files_excluded,
                },
                "policies": self.policies_checked,
                "findings": len(self.violations),
                "errors": len(self.errors),
            },
            "findings": [item.to_dict(self.root) for item in self.violations],
            "errors": [item.to_dict(self.root) for item in self.errors],
        }


def normalize_report_path(path: Path, root: Path) -> str:
    """Return a stable POSIX path relative to the invocation root when possible."""
    normalized = path.resolve(strict=False)
    try:
        relative = normalized.relative_to(root.resolve(strict=False))
    except ValueError:
        return normalized.as_posix()
    text = relative.as_posix()
    return text if text != "." else normalized.name
