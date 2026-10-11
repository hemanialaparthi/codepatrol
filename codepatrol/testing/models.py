"""Typed results produced by the generic subprocess runner."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

TestOutcome = Literal["PASS", "FAIL", "ERROR", "TIMEOUT", "INTERRUPTED"]


@dataclass(frozen=True)
class ExecutionError:
    """A structured failure to launch, capture, or control the command."""

    category: str
    message: str
    exception_type: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {"category": self.category, "message": self.message}
        if self.exception_type is not None:
            value["exception_type"] = self.exception_type
        return value


@dataclass(frozen=True)
class FrameworkDetails:
    """Optional framework knowledge supplied by a future adapter."""

    framework: str
    tests_total: int | None = None
    tests_passed: int | None = None
    tests_failed: int | None = None
    categories: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"framework": self.framework}
        for key in ("tests_total", "tests_passed", "tests_failed", "categories"):
            item = getattr(self, key)
            if item is not None:
                value[key] = item
        return value


@dataclass
class TestCommandResult:
    """Framework-neutral record of one supplied command execution."""

    outcome: TestOutcome
    arguments: list[str]
    working_directory: Path
    exit_code: int | None
    duration_seconds: float
    stdout_preview: str = ""
    stderr_preview: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_log: Path | None = None
    stderr_log: Path | None = None
    errors: list[ExecutionError] = field(default_factory=list)
    framework_details: FrameworkDetails | None = None

    @property
    def description(self) -> str:
        if self.outcome == "PASS":
            return "The supplied test command exited successfully."
        if self.outcome == "FAIL":
            return "The supplied test command exited with a nonzero status."
        if self.outcome == "TIMEOUT":
            return "The supplied test command exceeded its time limit."
        if self.outcome == "INTERRUPTED":
            return "The supplied test command was interrupted."
        return "The supplied test command could not be executed reliably."

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "outcome": self.outcome,
            "description": self.description,
            "arguments": self.arguments,
            "working_directory": str(self.working_directory),
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 6),
            "stdout": {
                "preview": self.stdout_preview,
                "truncated": self.stdout_truncated,
                "log_path": str(self.stdout_log) if self.stdout_log else None,
            },
            "stderr": {
                "preview": self.stderr_preview,
                "truncated": self.stderr_truncated,
                "log_path": str(self.stderr_log) if self.stderr_log else None,
            },
            "errors": [error.to_dict() for error in self.errors],
        }
        if self.framework_details is not None:
            value["framework_details"] = self.framework_details.to_dict()
        return value


class FrameworkResultAdapter(Protocol):
    """Extension point for optional, non-authoritative result interpretation."""

    def interpret(self, result: TestCommandResult) -> FrameworkDetails | None:
        """Return details learned from artifacts, or ``None`` when unavailable."""

