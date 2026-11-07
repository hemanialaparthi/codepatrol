"""One-batch deterministic repair orchestration."""

from dataclasses import dataclass, field
from pathlib import Path
import os

from codepatrol.evaluation import EvaluationResult, test_skip_reason
from codepatrol.errors import CodePatrolError
from codepatrol.fixing.edits import EditError, SourceEdit, apply_edits, plan_edits, replace_one_file
from codepatrol.fixing.staging import (
    StagedProject,
    StagingError,
    create_stage,
    infer_project_root,
    map_into_stage,
    validate_scope,
    validate_test_command,
)
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import CompiledPolicy
from codepatrol.scanner.models import CheckError, ScanResult
from codepatrol.scanner.project import ProjectScanner
from codepatrol.testing.runner import parse_command, run_test_command


@dataclass
class FixResult:
    initial: EvaluationResult
    staged: EvaluationResult | None = None
    edits_proposed: int = 0
    edits_applied: int = 0
    application: str = "not attempted"
    tests_ran: bool = False
    validation_location: str = "original project"
    source_preserved: bool = True
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "ERROR"
        if self.staged is not None:
            return self.staged.status if self.source_preserved else self.staged.status
        return self.initial.status

    @property
    def exit_code(self) -> int:
        return {"PASS": 0, "FAIL": 1, "ERROR": 2}[self.status]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "fix-1.0",
            "status": self.status,
            "initial": self.initial.to_dict(),
            "staged": self.staged.to_dict() if self.staged is not None else None,
            "edits": {"proposed": self.edits_proposed, "applied": self.edits_applied},
            "application": self.application,
            "tests": {"configured": self.initial.test_command is not None, "ran": self.tests_ran},
            "validation_location": self.validation_location,
            "source_preserved": self.source_preserved,
            "errors": [{"category": "fixing", "message": error} for error in self.errors],
        }


def run_fix(
    paths: list[Path],
    policies: list[CompiledPolicy],
    registry: LanguageRegistry,
    exclusions: list[str],
    *,
    test_command: str | None,
    test_cwd: Path | None,
    test_timeout: float,
    project_root: Path | None,
    artifact_root: Path,
) -> FixResult:
    scanner = ProjectScanner(registry)
    original = scanner.scan(paths, policies, exclusions)
    inferred_root: Path | None = None
    if test_command is not None:
        inferred_root = infer_project_root(paths, project_root)
    effective_test_cwd = test_cwd or inferred_root or Path.cwd().resolve()
    baseline = _evaluate(original, test_command, effective_test_cwd, test_timeout, artifact_root)
    result = FixResult(initial=baseline, tests_ran=baseline.test_command is not None)
    if original.status == "ERROR":
        result.application = "preserved: initial policy evaluation had errors"
        return result
    if baseline.test_command is not None and baseline.test_command.outcome in {"ERROR", "TIMEOUT", "INTERRUPTED"}:
        result.application = "preserved: baseline test execution was not reliable"
        return result
    if original.status == "PASS":
        result.application = "not needed: initial policy evaluation passed"
        return result

    try:
        root = inferred_root or infer_project_root(paths, project_root)
        effective_test_cwd = test_cwd or root
        validate_scope(root, paths, effective_test_cwd)
        edits = plan_edits(original, policies, {item.file.resolve(strict=False) for item in original.violations})
        result.edits_proposed = len(edits)
        if not edits:
            result.application = "preserved: violations have no applicable fixes"
            return result
        if len({edit.file for edit in edits}) != 1:
            raise StagingError("Version one supports fixes in one source file only.")
        if all(_is_noop(edit) for edit in edits):
            result.application = "preserved: proposed fixes are no-ops; violations remain unresolved"
            return result
        if test_command is not None:
            validate_test_command(parse_command(test_command), root)
        staged = create_stage(root)
        try:
            staged_file = map_into_stage(edits[0].file, root, staged.root)
            original_bytes = edits[0].file.read_bytes()
            staged_source = staged_file.read_bytes()
            staged_file.write_bytes(apply_edits(staged_source, [
                SourceEdit(staged_file, edit.start_byte, edit.end_byte, edit.replacement, edit.rule_id)
                for edit in edits
            ]))
            staged_paths = [map_into_stage(path, root, staged.root) for path in paths]
            staged_result = scanner.scan(staged_paths, policies, exclusions)
            staged_cwd = map_into_stage(effective_test_cwd, root, staged.root)
            staged_eval = _evaluate(
                staged_result,
                test_command,
                staged_cwd,
                test_timeout,
                artifact_root,
                environment=_staged_environment(staged.root),
            )
            result.staged = staged_eval
            result.validation_location = f"isolated staged project '{staged.root}'"
            result.tests_ran = result.tests_ran or staged_eval.test_command is not None
            if staged_eval.status != "PASS":
                result.application = "preserved: staged candidate was rejected"
                return result
            replace_one_file(edits[0].file, original_bytes, apply_edits(original_bytes, edits))
            result.edits_applied = len(edits)
            result.application = "applied after staged validation"
            result.source_preserved = False
            return result
        finally:
            staged.close()
    except (CodePatrolError, OSError, ValueError) as exc:
        result.errors.append(str(exc))
        result.application = "preserved: fix workflow failed"
        return result


def _evaluate(
    scan: ScanResult,
    command: str | None,
    cwd: Path,
    timeout: float,
    artifact_root: Path,
    *,
    environment: dict[str, str] | None = None,
) -> EvaluationResult:
    reason = test_skip_reason(scan)
    if reason is not None:
        return EvaluationResult(policy_scan=scan, test_command=None, test_skip_reason=reason)
    if command is None:
        return EvaluationResult(
            policy_scan=scan,
            test_command=None,
            test_skip_reason="tests not run: no test command was configured",
        )
    return EvaluationResult(
        policy_scan=scan,
        test_command=run_test_command(
            command,
            working_directory=cwd,
            timeout_seconds=timeout,
            artifact_root=artifact_root,
            environment=environment,
        ),
    )


def _staged_environment(stage_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(stage_root) + (os.pathsep + existing if existing else "")
    return environment


def _is_noop(edit: SourceEdit) -> bool:
    try:
        return edit.file.read_bytes()[edit.start_byte : edit.end_byte] == edit.replacement.encode("utf-8")
    except OSError:
        return False
