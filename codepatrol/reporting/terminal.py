"""Plain terminal output for scan results."""

from pathlib import Path
from textwrap import indent

from codepatrol.scanner.models import ScanResult, normalize_report_path

DIVIDER = "─" * 40


def render_terminal_report(project: Path, result: ScanResult) -> str:
    """Render a deterministic human-readable report."""
    lines = [
        "CodePatrol",
        DIVIDER,
        "",
        f"Project: {project}",
        f"Policies loaded: {result.policies_checked}",
        f"Files selected: {result.files_selected}",
        f"Files scanned: {result.files_scanned}",
        f"Files excluded: {result.files_excluded}",
    ]

    if result.violations:
        lines.extend(["", "Violations", DIVIDER])
        for violation in result.violations:
            lines.extend(
                [
                    "",
                    f"[{violation.rule_id}]",
                    "",
                    (
                        f"{normalize_report_path(violation.file, result.root)}:"
                        f"{violation.line}:{violation.column}-"
                        f"{violation.end_line}:{violation.end_column}"
                    ),
                    _format_source(
                        violation.matched_source, violation.column - 1
                    ),
                    "",
                    indent(violation.message, "    "),
                ]
            )

    if result.errors:
        lines.extend(["", "Errors", DIVIDER])
        for error in result.errors:
            context = ""
            if error.file is not None:
                context = normalize_report_path(error.file, result.root)
            if error.start is not None:
                context += f":{error.start.line}:{error.start.column}"
            lines.extend(
                [
                    "",
                    f"[{error.category}]" + (f" {context}" if context else ""),
                    indent(error.message, "    "),
                ]
            )

    lines.extend(
        [
            "",
            DIVIDER,
            f"Files scanned: {result.files_scanned}",
            f"Files excluded: {result.files_excluded}",
            f"Policies checked: {result.policies_checked}",
            f"Findings: {len(result.violations)}",
            f"Violations: {len(result.violations)}",
            f"Errors: {len(result.errors)}",
            "",
            {"PASS": "PASSED", "FAIL": "FAILED", "ERROR": "ERROR"}[result.status],
        ]
    )
    return "\n".join(lines)


def _format_source(source: str, original_indent: int) -> str:
    source_lines = source.splitlines()
    for index in range(1, len(source_lines)):
        line = source_lines[index]
        prefix = line[:original_indent]
        if len(prefix) == original_indent and not prefix.strip():
            source_lines[index] = line[original_indent:]
    return indent("\n".join(source_lines), "    ")
