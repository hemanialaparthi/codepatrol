import json
from pathlib import Path

import pytest

from codepatrol.cli import main
from codepatrol.evaluation import EvaluationResult
from codepatrol.reporting.terminal import (
    render_evaluation_terminal,
    render_terminal_report,
)
from codepatrol.scanner import CheckError, ScanResult, SourceLocation, Violation
from codepatrol.testing import ExecutionError, TestCommandResult as CommandResult


def finding(root: Path, index: int = 1, *, source: str = "print(value)") -> Violation:
    return Violation(
        rule_id=f"no-print-{index}",
        message=f"Use logging [rule {index}].",
        file=root / f"módulo-{index}.py",
        line=index,
        column=3,
        end_line=index + source.count("\n"),
        end_column=8,
        matched_source=source,
    )


def scan(root: Path, *, failed: bool = False) -> ScanResult:
    return ScanResult(
        violations=[finding(root)] if failed else [],
        files_scanned=2,
        files_selected=3,
        files_excluded=1,
        policies_checked=1,
        root=root,
    )


def command_result(
    root: Path,
    outcome: str,
    *,
    errors: list[ExecutionError] | None = None,
    truncated: bool = False,
) -> CommandResult:
    return CommandResult(
        outcome=outcome,  # type: ignore[arg-type]
        arguments=["python", "-m", "example", "[literal]"],
        working_directory=root,
        exit_code={"PASS": 0, "FAIL": 3}.get(outcome),
        duration_seconds=1.2345,
        stdout_preview="stdout [bold]\n",
        stderr_preview="stderr\x1b[31m\n",
        stdout_truncated=truncated,
        stderr_truncated=False,
        stdout_log=root / "stdout.log",
        stderr_log=root / "stderr.log",
        errors=errors or [],
    )


def test_scan_only_pass_fail_and_error_lead_with_overall_status(tmp_path):
    passed = render_terminal_report(tmp_path, scan(tmp_path), color=False)
    failed = render_terminal_report(tmp_path, scan(tmp_path, failed=True), color=False)
    errored_scan = scan(tmp_path)
    errored_scan.errors.append(
        CheckError(
            category="parsing",
            message="Invalid [syntax].",
            file=tmp_path / "broken.py",
            start=SourceLocation(4, 2),
            end=SourceLocation(4, 3),
        )
    )
    errored = render_terminal_report(tmp_path, errored_scan, color=False)

    assert passed.startswith("PASS — Policies passed with no violations.")
    assert failed.startswith("FAIL — 1 policy violation remains.")
    assert errored.startswith("ERROR — Policy checking encountered 1 error.")
    assert "Checking errors\n1. parsing — broken.py:4:2" in errored
    assert "Findings:" not in passed + failed + errored
    assert "FAILED" not in passed + failed + errored


@pytest.mark.parametrize(
    ("policy_failed", "test_outcome", "expected"),
    [
        (False, "PASS", "PASS — Policies passed and the test command exited successfully."),
        (True, "PASS", "FAIL — 1 policy violation remains. The test command passed."),
        (False, "FAIL", "FAIL — Policies passed, but the test command failed."),
        (
            True,
            "FAIL",
            "FAIL — 1 policy violation remains, and the test command failed.",
        ),
    ],
)
def test_policy_and_test_pass_fail_combinations(
    tmp_path, policy_failed, test_outcome, expected
):
    result = EvaluationResult(
        policy_scan=scan(tmp_path, failed=policy_failed),
        test_command=command_result(tmp_path, test_outcome),
    )

    output = render_evaluation_terminal(result, color=False)

    assert output.startswith(expected)
    assert f"Test command: {test_outcome}" in output
    assert "Exit code:" in output
    if test_outcome == "PASS":
        assert "stdout preview" not in output
    else:
        assert "stdout preview:" in output


def test_skipped_test_includes_reason_and_policy_error(tmp_path):
    policy = scan(tmp_path)
    policy.errors.append(CheckError(category="discovery", message="Input missing."))
    reason = "Test execution was skipped because discovery failed."
    result = EvaluationResult(policy_scan=policy, test_command=None, test_skip_reason=reason)

    output = render_evaluation_terminal(result, color=False)

    assert output.startswith(f"ERROR — {reason}")
    assert "Test command: SKIPPED" in output
    assert output.index("Checking errors") < output.index("Test command\n")


@pytest.mark.parametrize(
    ("outcome", "error", "expected"),
    [
        (
            "ERROR",
            ExecutionError("launch", "Executable [missing]."),
            "ERROR — The test command could not be started.",
        ),
        ("TIMEOUT", None, "ERROR — The test command exceeded its time limit."),
        ("INTERRUPTED", None, "ERROR — The test command was interrupted."),
    ],
)
def test_unsuccessful_execution_shows_diagnostics(tmp_path, outcome, error, expected):
    result = command_result(tmp_path, outcome, errors=[error] if error else [])
    evaluation = EvaluationResult(policy_scan=scan(tmp_path), test_command=result)

    output = render_evaluation_terminal(evaluation, color=False)

    assert output.startswith(expected)
    assert "Command: python -m example '[literal]'" in output
    assert "stdout preview:" in output
    assert "stderr preview:" in output
    assert "Full stdout log:" in output
    assert "stdout.log" in output
    assert "\\x1b[31m" in output
    assert "\x1b[31m" not in output
    if error:
        assert "Execution error [launch]: Executable [missing]." in output


def test_multiline_unicode_and_markup_like_text_are_literal_and_numbered(tmp_path):
    source = "if ready:\n    print('[bold] café')"
    result = scan(tmp_path)
    result.violations = [finding(tmp_path, source=source)]

    output = render_terminal_report(tmp_path, result, color=False, width=42)

    assert "1. módulo-1.py:1:3" in output
    assert "Rule: no-print-1" in output
    assert "Use logging [rule 1]." in output
    assert "if ready:" in output
    assert "print('[bold] café')" in output
    assert "  1 " in output and "  2 " in output
    assert "\x1b[" not in output


def test_default_finding_limit_and_verbose_expansion(tmp_path):
    result = scan(tmp_path)
    result.violations = [finding(tmp_path, index) for index in range(1, 13)]

    compact = render_terminal_report(tmp_path, result, color=False)
    verbose = render_terminal_report(tmp_path, result, verbose=True, color=False)

    assert "10. módulo-10.py" in compact
    assert "módulo-11.py" not in compact
    assert "2 additional violations omitted; use --verbose to show all." in compact
    assert "12. módulo-12.py" in verbose
    assert "módulo-1.py:1:3-1:8" in verbose
    assert "Files selected: 3" in verbose
    assert "Files excluded: 1" in verbose


def test_verbose_success_expands_command_output_and_paths(tmp_path):
    evaluation = EvaluationResult(
        policy_scan=scan(tmp_path),
        test_command=command_result(tmp_path, "PASS", truncated=True),
    )

    output = render_evaluation_terminal(evaluation, verbose=True, color=False)

    assert "Project:" in output
    assert "Working directory:" in output
    assert "Arguments:" in output
    assert "[3] [literal]" in output
    assert "stdout preview (truncated):" in output
    assert "Full stderr log:" in output
    assert "stderr.log" in output


def test_no_color_environment_and_saved_report_message(tmp_path, monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("codepatrol.reporting.terminal.sys.stdout", Tty())
    report = tmp_path / "results" / "report.json"

    output = render_terminal_report(
        tmp_path,
        scan(tmp_path),
        saved_report=report,
        width=36,
    )

    assert "\x1b[" not in output
    assert "Saved report:" in output
    assert "report.json" in output.replace("\n", "")


def test_verbose_does_not_change_json_or_exit_code(tmp_path, capsys):
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """rules:
  - id: no-print
    language: python
    pattern: print($X)
    message: Use logging.
""",
        encoding="utf-8",
    )
    source = tmp_path / "source.py"
    source.write_text("print('value')\n", encoding="utf-8")
    arguments = ["scan", str(source), "--policies", str(policy), "--json"]

    ordinary_code = main(arguments)
    ordinary = capsys.readouterr()
    verbose_code = main([*arguments, "--verbose"])
    verbose = capsys.readouterr()

    assert ordinary_code == verbose_code == 1
    assert json.loads(ordinary.out) == json.loads(verbose.out)
    assert ordinary.out == verbose.out
    assert ordinary.err == verbose.err == ""
