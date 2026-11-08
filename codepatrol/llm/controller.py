"""One-request LLM repair orchestration over the existing candidate pipeline."""

from dataclasses import dataclass
import difflib
from pathlib import Path

from codepatrol.fixing.controller import _evaluate, _staged_environment
from codepatrol.fixing.edits import SourceEdit, apply_edits, replace_one_file
from codepatrol.fixing.staging import (
    create_stage,
    infer_project_root,
    map_into_stage,
    validate_scope,
    validate_test_command,
)
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.llm.client import (
    LLMConfigurationError,
    LLMProviderError,
    endpoint_identifier,
    installed_version,
    request_repair,
)
from codepatrol.llm.debug import DebugArtifactError, write_debug_response
from codepatrol.llm.models import LLMFixResult, LLMRepairConfig
from codepatrol.llm.prompt import build_request
from codepatrol.llm.response import LLMResponseError, parse_response
from codepatrol.policy.models import CompiledPolicy
from codepatrol.scanner.project import ProjectScanner
from codepatrol.testing.runner import parse_command


@dataclass(frozen=True)
class LLMRunConfig:
    repair: LLMRepairConfig
    project_root: Path | None
    test_command: str | None
    test_cwd: Path | None
    test_timeout: float
    artifact_root: Path
    debug_response_path: Path | None = None


def run_llm_fix(
    paths: list[Path],
    policies: list[CompiledPolicy],
    registry: LanguageRegistry,
    exclusions: list[str],
    config: LLMRunConfig,
) -> LLMFixResult:
    root = infer_project_root(paths, config.project_root)
    effective_cwd = config.test_cwd or root
    validate_scope(root, paths, effective_cwd)
    scanner = ProjectScanner(registry)
    original = scanner.scan(paths, policies, exclusions)
    baseline = _evaluate(
        original, config.test_command, effective_cwd, config.test_timeout, config.artifact_root
    )
    result = LLMFixResult(
        initial=baseline,
        tests_ran=baseline.test_command is not None,
        model=config.repair.model,
        api_base_configured=config.repair.api_base is not None,
        endpoint_id=endpoint_identifier(config.repair.api_base),
        timeout=config.repair.timeout,
        max_tokens=config.repair.max_tokens,
        temperature=config.repair.temperature,
        json_mode=config.repair.json_mode,
        debug_response_path=(
            str(config.debug_response_path) if config.debug_response_path else None
        ),
        distribution="litellm-core",
        distribution_version=installed_version(),
    )
    if original.status == "ERROR":
        result.application = "preserved: initial policy evaluation had errors"
        return result
    if baseline.test_command is not None and baseline.test_command.outcome in {"ERROR", "TIMEOUT", "INTERRUPTED"}:
        result.application = "preserved: baseline test execution was not reliable"
        return result
    if baseline.status == "PASS":
        result.application = "not needed: initial policy and configured tests passed"
        return result
    if len(original.selected_files) != 1:
        result.errors.append(
            "LLM repair requires exactly one discovered permitted Python source file; "
            f"found {len(original.selected_files)}. Narrow the input path or exclusions."
        )
        result.application = "preserved: ambiguous source selection"
        return result
    source_file = original.selected_files[0]
    if source_file.suffix != ".py":
        result.errors.append("LLM repair permits only one Python source file.")
        result.application = "preserved: source is not Python"
        return result
    if config.test_command is not None:
        validate_test_command(parse_command(config.test_command), root)
    try:
        with source_file.open("r", encoding="utf-8", newline="") as handle:
            source = handle.read()
        request = build_request(source_file, source, original, policies, baseline.test_command, root)
        result.request_attempted = True
        response = request_repair(config.repair, request)
        result.provider_response_received = True
        result.distribution = response.distribution
        result.distribution_version = response.version
        result.finish_reason = response.finish_reason
        result.response_content_kind = response.content_kind
        result.usage = response.usage
        result.elapsed_seconds = response.elapsed_seconds
        if config.debug_response_path is not None:
            try:
                write_debug_response(
                    config.debug_response_path,
                    model=config.repair.model,
                    endpoint_id=result.endpoint_id or "unknown",
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                    content=response.content,
                    content_kind=response.content_kind,
                )
            except DebugArtifactError as exc:
                result.errors.append(str(exc))
                result.application = (
                    "preserved: response debug artifact could not be written"
                )
                return result
        parsed = parse_response(response)
        result.candidate_parsed = True
        result.explanation = parsed.explanation
        result.response_outcome = parsed.outcome
        if parsed.outcome == "needs_guidance":
            result.needs_guidance = True
            result.application = "preserved: model requested guidance"
            return result
        assert parsed.replacement is not None
        candidate = parsed.replacement
        if candidate == source:
            result.application = "preserved: candidate was a no-op; violations remain unresolved"
            return result
        result.candidate_diff = _candidate_diff(source_file, source, candidate)
        staged = create_stage(root)
        try:
            staged_file = map_into_stage(source_file, root, staged.root)
            staged_bytes = staged_file.read_bytes()
            staged_file.write_bytes(
                apply_edits(staged_bytes, [SourceEdit(staged_file, 0, len(staged_bytes), candidate, "llm-repair")])
            )
            staged_scan = scanner.scan([map_into_stage(source_file, root, staged.root)], policies, exclusions)
            result.staged = _evaluate(
                staged_scan,
                config.test_command,
                map_into_stage(effective_cwd, root, staged.root),
                config.test_timeout,
                config.artifact_root,
                environment=_staged_environment(staged.root),
            )
            result.validation_location = f"isolated staged project '{staged.root}'"
            result.tests_ran = result.tests_ran or result.staged.test_command is not None
            if result.staged.status != "PASS":
                result.application = "preserved: staged candidate was rejected"
                return result
            original_bytes = source_file.read_bytes()
            replace_one_file(source_file, original_bytes, candidate.encode("utf-8"))
            result.application = "applied after staged validation"
            result.source_preserved = False
            return result
        finally:
            staged.close()
    except (LLMConfigurationError, LLMProviderError, LLMResponseError, OSError, UnicodeError, ValueError) as exc:
        result.errors.append(str(exc))
        result.application = "preserved: LLM repair workflow failed"
        return result


def _candidate_diff(path: Path, original: str, candidate: str) -> str:
    sentinel = "\x00CODEPATROL_NO_FINAL_NEWLINE"
    while sentinel in original or sentinel in candidate:
        sentinel += "_"

    original_lines = original.splitlines()
    candidate_lines = candidate.splitlines()
    if original_lines and not original.endswith(("\n", "\r")):
        original_lines[-1] += sentinel
    if candidate_lines and not candidate.endswith(("\n", "\r")):
        candidate_lines[-1] += sentinel

    diff = difflib.unified_diff(
        [f"{line}\n" for line in original_lines],
        [f"{line}\n" for line in candidate_lines],
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name} (candidate)",
    )
    rendered: list[str] = []
    marker = "\\ No newline at end of file\n"
    for line in diff:
        if line[:1] in {" ", "+", "-"} and sentinel in line:
            rendered.append(line.replace(sentinel, ""))
            rendered.append(marker)
        else:
            rendered.append(line)
    return "".join(rendered)
