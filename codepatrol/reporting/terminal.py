"""Readable Rich terminal reports built directly from result models."""

import os
import shlex
import shutil
import sys
from io import StringIO
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from codepatrol.evaluation import EvaluationResult
from codepatrol.scanner.models import (
    CheckError,
    ScanResult,
    Violation,
    normalize_report_path,
)
from codepatrol.testing import TestCommandResult

DEFAULT_FINDING_LIMIT = 10
Status = Literal["PASS", "FAIL", "ERROR"]

STATUS_STYLES = {
    "PASS": "bold green",
    "FAIL": "bold yellow",
    "ERROR": "bold red",
    "TIMEOUT": "bold red",
    "INTERRUPTED": "bold red",
    "SKIPPED": "bold",
}


def render_terminal_report(
    project: Path,
    result: ScanResult,
    *,
    verbose: bool = False,
    finding_limit: int = DEFAULT_FINDING_LIMIT,
    saved_report: Path | None = None,
    report_error: str | None = None,
    color: bool | None = None,
    width: int | None = None,
) -> str:
    """Render a scan-only result without changing the underlying scan model."""
    console, stream = _console(color=color, width=width)
    overall: Status = "ERROR" if report_error is not None else result.status
    explanation = (
        f"The report could not be saved: {report_error}"
        if report_error is not None
        else _scan_explanation(result)
    )
    _render_heading(console, overall, explanation)
    _render_scan_summary(console, result)
    _render_scan_details(
        console,
        project,
        result,
        verbose=verbose,
        finding_limit=finding_limit,
    )
    if report_error is not None:
        _section(console, "Reporting errors")
        _literal(console, report_error, style="red")
    if saved_report is not None:
        _render_saved_report(console, saved_report)
    return stream.getvalue().rstrip("\n")


def render_evaluation_terminal(
    result: EvaluationResult,
    *,
    verbose: bool = False,
    finding_limit: int = DEFAULT_FINDING_LIMIT,
    saved_report: Path | None = None,
    color: bool | None = None,
    width: int | None = None,
) -> str:
    """Render one combined status followed by policy and command evidence."""
    console, stream = _console(color=color, width=width)
    _render_heading(console, result.status, _evaluation_explanation(result))
    _render_scan_summary(
        console,
        result.policy_scan,
        test=result.test_command,
        test_skipped=result.test_command is None,
    )
    if result.errors:
        summary = Text("Evaluation errors: ")
        summary.append(str(len(result.errors)), style="bold red")
        console.print(summary)
    _render_scan_details(
        console,
        result.policy_scan.root,
        result.policy_scan,
        verbose=verbose,
        finding_limit=finding_limit,
    )
    if result.test_command is None:
        _section(console, "Test command")
        _literal(console, result.test_skip_reason or "Test execution was skipped.")
    else:
        _render_test_result(console, result.test_command, verbose=verbose)
    if result.errors:
        _section(console, "Evaluation errors")
        for index, error in enumerate(result.errors, start=1):
            _literal(console, f"{index}. {error.category}: {error.message}", style="red")
    if saved_report is not None:
        _render_saved_report(console, saved_report)
    return stream.getvalue().rstrip("\n")


def render_saved_report_message(path: Path) -> str:
    """Return a control-safe plain message for JSON-mode stderr."""
    return f"Saved report: {safe_terminal_text(str(path))}"


def safe_terminal_text(value: object) -> str:
    """Make control characters visible while retaining Unicode and whitespace."""
    text = str(value)
    rendered: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character in {"\n", "\t"}:
            rendered.append(character)
        elif codepoint < 32 or codepoint == 127 or 128 <= codepoint <= 159:
            rendered.append(f"\\x{codepoint:02x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _console(*, color: bool | None, width: int | None) -> tuple[Console, StringIO]:
    stream = StringIO()
    use_color = _color_enabled() if color is None else color
    terminal_width = width or shutil.get_terminal_size((100, 24)).columns
    console = Console(
        file=stream,
        force_terminal=use_color,
        color_system="standard" if use_color else None,
        no_color=not use_color,
        width=max(30, terminal_width),
        markup=False,
        highlight=False,
    )
    return console, stream


def _color_enabled() -> bool:
    return (
        "NO_COLOR" not in os.environ
        and os.environ.get("TERM", "").lower() != "dumb"
        and sys.stdout.isatty()
    )


def _render_heading(console: Console, status: Status, explanation: str) -> None:
    heading = Text()
    heading.append(status, style=STATUS_STYLES[status])
    heading.append(" — ")
    heading.append(safe_terminal_text(explanation))
    console.print(heading)
    console.print()


def _render_scan_summary(
    console: Console,
    result: ScanResult,
    *,
    test: TestCommandResult | None = None,
    test_skipped: bool = False,
) -> None:
    policy = Text("Policy: ")
    policy.append(result.status, style=STATUS_STYLES[result.status])
    policy.append(f" · Violations: {len(result.violations)}")
    console.print(policy)
    if test is not None:
        test_line = Text("Test command: ")
        test_line.append(test.outcome, style=STATUS_STYLES[test.outcome])
        console.print(test_line)
    elif test_skipped:
        test_line = Text("Test command: ")
        test_line.append("SKIPPED", style=STATUS_STYLES["SKIPPED"])
        console.print(test_line)
    console.print(
        Text(
            f"Files scanned: {result.files_scanned} · "
            f"Policies checked: {result.policies_checked}"
        )
    )
    if result.errors:
        errors = Text("Checking errors: ")
        errors.append(str(len(result.errors)), style="bold red")
        console.print(errors)


def _render_scan_details(
    console: Console,
    project: Path,
    result: ScanResult,
    *,
    verbose: bool,
    finding_limit: int,
) -> None:
    if verbose:
        _section(console, "Scan details")
        _labeled(console, "Project", project)
        _labeled(console, "Files selected", result.files_selected)
        _labeled(console, "Files excluded", result.files_excluded)

    if result.violations:
        _section(console, "Policy violations")
        displayed = result.violations if verbose else result.violations[:finding_limit]
        for index, violation in enumerate(displayed, start=1):
            _render_violation(console, index, violation, result.root, verbose=verbose)
        omitted = len(result.violations) - len(displayed)
        if omitted:
            noun = "violation" if omitted == 1 else "violations"
            _literal(
                console,
                f"{omitted} additional {noun} omitted; use --verbose to show all.",
                style="yellow",
            )

    if result.errors:
        _section(console, "Checking errors")
        for index, error in enumerate(result.errors, start=1):
            _render_check_error(console, index, error, result.root, verbose=verbose)


def _render_violation(
    console: Console,
    index: int,
    violation: Violation,
    root: Path,
    *,
    verbose: bool,
) -> None:
    path = normalize_report_path(violation.file, root)
    if verbose:
        location = (
            f"{path}:{violation.line}:{violation.column}-"
            f"{violation.end_line}:{violation.end_column}"
        )
    else:
        location = f"{path}:{violation.line}:{violation.column}"
    _literal(console, f"{index}. {location}", style="bold")
    _labeled(console, "Rule", violation.rule_id, indent=3)
    if violation.message:
        _literal(console, violation.message, indent=3)
    snippet = safe_terminal_text(violation.matched_source)
    console.print(
        Syntax(
            snippet,
            "python",
            line_numbers=True,
            start_line=violation.line,
            word_wrap=True,
            background_color="default",
        )
    )


def _render_check_error(
    console: Console,
    index: int,
    error: CheckError,
    root: Path,
    *,
    verbose: bool,
) -> None:
    location = ""
    if error.file is not None:
        location = normalize_report_path(error.file, root)
    if error.start is not None:
        location += f":{error.start.line}:{error.start.column}"
        if verbose and error.end is not None:
            location += f"-{error.end.line}:{error.end.column}"
    heading = f"{index}. {error.category}"
    if location:
        heading += f" — {location}"
    _literal(console, heading, style="bold red")
    if error.policy_id is not None:
        _labeled(console, "Rule", error.policy_id, indent=3)
    _literal(console, error.message, indent=3)


def _render_test_result(
    console: Console, result: TestCommandResult, *, verbose: bool
) -> None:
    _section(console, "Test command")
    outcome = Text()
    outcome.append(result.outcome, style=STATUS_STYLES[result.outcome])
    outcome.append(f" · Exit code: {_exit_code(result.exit_code)}")
    outcome.append(f" · Duration: {result.duration_seconds:.3f}s")
    console.print(outcome)
    _literal(console, _test_outcome_explanation(result))

    show_diagnostics = verbose or result.outcome != "PASS"
    if not show_diagnostics:
        return
    _labeled(console, "Command", _format_command(result.arguments))
    if verbose:
        _labeled(console, "Working directory", result.working_directory)
        _literal(console, "Arguments:")
        for index, argument in enumerate(result.arguments):
            _literal(console, f"[{index}] {argument}", indent=3)
    for error in result.errors:
        _literal(
            console,
            f"Execution error [{error.category}]: {error.message}",
            style="red",
        )
    _render_stream_preview(
        console,
        "stdout",
        result.stdout_preview,
        result.stdout_truncated,
        result.stdout_log,
    )
    _render_stream_preview(
        console,
        "stderr",
        result.stderr_preview,
        result.stderr_truncated,
        result.stderr_log,
    )


def _render_stream_preview(
    console: Console,
    name: str,
    preview: str,
    truncated: bool,
    log_path: Path | None,
) -> None:
    suffix = " (truncated)" if truncated else ""
    _literal(console, f"{name} preview{suffix}:", style="bold")
    _literal(console, preview if preview else "(empty)", indent=3)
    if log_path is not None:
        _labeled(console, f"Full {name} log", log_path)


def _render_saved_report(console: Console, path: Path) -> None:
    console.print()
    _labeled(console, "Saved report", path, style="green")


def _section(console: Console, title: str) -> None:
    console.print()
    console.print(Text(title, style="bold"))


def _labeled(
    console: Console,
    label: str,
    value: object,
    *,
    indent: int = 0,
    style: str | None = None,
) -> None:
    text = Text(" " * indent + f"{label}: ", style=style or "bold")
    text.append(safe_terminal_text(value))
    console.print(text)


def _literal(
    console: Console,
    value: object,
    *,
    indent: int = 0,
    style: str | None = None,
) -> None:
    prefix = " " * indent
    lines = safe_terminal_text(value).split("\n")
    for line in lines:
        console.print(Text(prefix + line, style=style))


def _scan_explanation(result: ScanResult) -> str:
    violations = len(result.violations)
    errors = len(result.errors)
    if result.status == "PASS":
        return "Policies passed with no violations."
    if result.status == "FAIL":
        return f"{_remaining(violations, 'policy violation')}."
    if violations:
        verb = "was" if violations == 1 else "were"
        return (
            f"Policy checking encountered {_count(errors, 'error')}; "
            f"{_count(violations, 'policy violation')} {verb} also found."
        )
    return f"Policy checking encountered {_count(errors, 'error')}."


def _evaluation_explanation(result: EvaluationResult) -> str:
    policy = result.policy_scan
    test = result.test_command
    violations = len(policy.violations)
    if result.status == "PASS":
        return "Policies passed and the test command exited successfully."
    if result.status == "FAIL" and test is not None:
        policy_failed = policy.status == "FAIL"
        test_failed = test.outcome == "FAIL"
        if policy_failed and test_failed:
            return (
                f"{_remaining(violations, 'policy violation')}, and the test "
                "command failed."
            )
        if policy_failed:
            return (
                f"{_remaining(violations, 'policy violation')}. "
                "The test command passed."
            )
        return "Policies passed, but the test command failed."
    if result.test_skip_reason is not None:
        return result.test_skip_reason
    if test is not None and test.outcome in {"ERROR", "TIMEOUT", "INTERRUPTED"}:
        return _test_outcome_explanation(test)
    if policy.status == "ERROR":
        explanation = f"Policy checking encountered {_count(len(policy.errors), 'error')}."
        if test is not None:
            if test.outcome == "PASS":
                explanation += " The test command passed."
            elif test.outcome == "FAIL":
                explanation += " The test command failed."
        return explanation
    if result.errors:
        return f"Evaluation encountered {_count(len(result.errors), 'error')}."
    return "The evaluation could not be completed reliably."


def _test_outcome_explanation(result: TestCommandResult) -> str:
    if result.outcome == "PASS":
        return "The supplied test command exited successfully."
    if result.outcome == "FAIL":
        return "The supplied test command exited with a nonzero status."
    if result.outcome == "TIMEOUT":
        return "The test command exceeded its time limit."
    if result.outcome == "INTERRUPTED":
        return "The test command was interrupted."
    if any(error.category == "launch" for error in result.errors):
        return "The test command could not be started."
    return "The test command could not be executed reliably."


def _format_command(arguments: list[str]) -> str:
    return shlex.join(arguments) if arguments else "(unavailable)"


def _exit_code(exit_code: int | None) -> str:
    return str(exit_code) if exit_code is not None else "unavailable"


def _count(value: int, singular: str) -> str:
    return f"{value} {singular if value == 1 else singular + 's'}"


def _remaining(value: int, singular: str) -> str:
    verb = "remains" if value == 1 else "remain"
    return f"{_count(value, singular)} {verb}"
