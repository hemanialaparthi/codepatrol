"""Orchestration and typed reporting for policy-plus-test evaluations."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from codepatrol.scanner import ScanResult
from codepatrol.testing import TestCommandResult, run_test_command

EvaluationStatus = Literal["PASS", "FAIL", "ERROR"]


@dataclass(frozen=True)
class EvaluationError:
    category: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category, "message": self.message}


@dataclass
class EvaluationResult:
    """A separately versioned envelope; the embedded scan report stays v1."""

    policy_scan: ScanResult
    test_command: TestCommandResult | None
    test_skip_reason: str | None = None
    errors: list[EvaluationError] = field(default_factory=list)

    @property
    def status(self) -> EvaluationStatus:
        if self.errors or self.policy_scan.status == "ERROR":
            return "ERROR"
        if self.test_command is None:
            return "ERROR"
        if self.test_command.outcome in {"ERROR", "TIMEOUT", "INTERRUPTED"}:
            return "ERROR"
        if self.policy_scan.status == "FAIL" or self.test_command.outcome == "FAIL":
            return "FAIL"
        return "PASS"

    @property
    def exit_code(self) -> int:
        return {"PASS": 0, "FAIL": 1, "ERROR": 2}[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evaluation-1.0",
            "status": self.status,
            "policy_scan": self.policy_scan.to_dict(),
            "test_command": (
                self.test_command.to_dict() if self.test_command is not None else None
            ),
            "errors": [error.to_dict() for error in self.errors],
            "test_skip_reason": self.test_skip_reason,
        }


def evaluate_scan(
    policy_scan: ScanResult,
    test_command: str,
    *,
    test_working_directory: Path,
    test_timeout_seconds: float = 60.0,
    artifact_root: Path | None = None,
) -> EvaluationResult:
    """Run functional verification when policy errors do not block it."""
    reason = test_skip_reason(policy_scan)
    if reason is not None:
        return EvaluationResult(
            policy_scan=policy_scan,
            test_command=None,
            test_skip_reason=reason,
        )
    test_result = run_test_command(
        test_command,
        working_directory=test_working_directory,
        timeout_seconds=test_timeout_seconds,
        artifact_root=artifact_root,
    )
    return EvaluationResult(policy_scan=policy_scan, test_command=test_result)


def test_skip_reason(policy_scan: ScanResult) -> str | None:
    """Return why tests are unsafe to run; syntax-only scan errors are allowed."""
    blocking = [error for error in policy_scan.errors if error.category != "parsing"]
    if not blocking:
        return None
    categories = ", ".join(sorted({error.category for error in blocking}))
    return (
        "Test execution was skipped because policy checking had blocking "
        f"error categories: {categories}."
    )


def evaluation_exit_code(result: EvaluationResult) -> int:
    """Public helper for callers that need CodePatrol's combined exit contract."""
    return result.exit_code
