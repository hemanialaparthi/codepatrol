"""Typed models for the bounded LLM repair workflow."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from codepatrol.evaluation import EvaluationResult
from codepatrol.policy.models import CompiledPolicy
from codepatrol.scanner.models import Violation


MAX_SOURCE_BYTES = 256 * 1024
MAX_REQUEST_TEXT_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_TEST_FEEDBACK_CHARS = 8 * 1024
MAX_INSTRUCTION_CHARS = 32 * 1024


@dataclass(frozen=True)
class LLMRepairConfig:
    model: str
    api_base: str | None
    api_key_env: str | None
    timeout: float
    max_tokens: int
    temperature: float | None
    json_mode: bool = False


@dataclass(frozen=True)
class RepairRequest:
    source_file: Path
    source: str
    policies: tuple[dict[str, Any], ...]
    test_feedback: str
    project_instructions: str


@dataclass(frozen=True)
class ProviderResponse:
    content: str | None
    finish_reason: str | None
    content_kind: str = "string"
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    distribution: str = "litellm-core"
    version: str = "unknown"


@dataclass(frozen=True)
class ParsedRepair:
    outcome: Literal["candidate", "needs_guidance"]
    replacement: str | None = None
    explanation: str = ""


@dataclass
class LLMFixResult:
    initial: EvaluationResult
    request_attempted: bool = False
    provider_response_received: bool = False
    candidate_parsed: bool = False
    needs_guidance: bool = False
    staged: EvaluationResult | None = None
    application: str = "not attempted"
    source_preserved: bool = True
    tests_ran: bool = False
    validation_location: str = "original project"
    model: str | None = None
    api_base_configured: bool = False
    endpoint_id: str | None = None
    timeout: float | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    json_mode: bool = False
    debug_response_path: str | None = None
    distribution: str | None = None
    distribution_version: str | None = None
    finish_reason: str | None = None
    response_content_kind: str | None = None
    response_outcome: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float | None = None
    candidate_diff: str | None = None
    explanation: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "ERROR"
        if self.staged is not None:
            return self.staged.status
        if self.needs_guidance or self.request_attempted:
            return "FAIL"
        return self.initial.status

    @property
    def exit_code(self) -> int:
        return {"PASS": 0, "FAIL": 1, "ERROR": 2}[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "llm-fix-1.0",
            "status": self.status,
            "initial": self.initial.to_dict(),
            "request": {
                "attempted": self.request_attempted,
                "model": self.model,
                "api_base_configured": self.api_base_configured,
                "endpoint_id": self.endpoint_id,
                "timeout": self.timeout,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "json_mode": self.json_mode,
                "debug_response_path": self.debug_response_path,
            },
            "provider": {
                "response_received": self.provider_response_received,
                "distribution": self.distribution,
                "version": self.distribution_version,
                "elapsed_seconds": self.elapsed_seconds,
                "finish_reason": self.finish_reason,
                "usage": self.usage,
                "content_kind": self.response_content_kind,
            },
            "response": {
                "candidate_parsed": self.candidate_parsed,
                "outcome": self.response_outcome,
                "explanation": self.explanation,
            },
            "staged": self.staged.to_dict() if self.staged is not None else None,
            "application": self.application,
            "tests": {
                "configured": self.initial.test_command is not None,
                "ran": self.tests_ran,
                "validation_location": self.validation_location,
            },
            "candidate_diff": self.candidate_diff,
            "source_preserved": self.source_preserved,
            "errors": [{"category": "llm-repair", "message": item} for item in self.errors],
        }


def policy_context(policies: list[CompiledPolicy], violations: list[Violation]) -> tuple[dict[str, Any], ...]:
    ids = {violation.rule_id for violation in violations}
    return tuple(
        {
            "id": compiled.policy.id,
            "language": compiled.policy.language,
            "mechanism": "pattern" if compiled.policy.pattern is not None else "xpath",
            "expression": (
                compiled.policy.pattern
                if compiled.pattern is not None
                else compiled.policy.xpath
            ),
            "message": compiled.policy.message,
            "fix": compiled.policy.fix.replacement if compiled.policy.fix is not None else None,
            "violations": [
                {
                    "line": violation.line,
                    "column": violation.column,
                    "end_line": violation.end_line,
                    "end_column": violation.end_column,
                    "matched_source": violation.matched_source,
                }
                for violation in violations
                if violation.rule_id == compiled.policy.id
            ],
        }
        for compiled in policies
        if compiled.policy.id in ids
    )
