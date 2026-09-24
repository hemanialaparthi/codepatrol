"""Plain terminal output for scan results."""

from pathlib import Path
from textwrap import indent

from codepatrol.scanner.models import ScanResult

DIVIDER = "─" * 40


def render_terminal_report(project: Path, result: ScanResult) -> str:
    lines = [
        "CodePatrol",
        DIVIDER,
        "",
        f"Project: {project}",
        f"Policies loaded: {result.policies_checked}",
        f"Files scanned: {result.files_scanned}",
    ]

    if result.violations:
        lines.extend(["", "Violations", DIVIDER])
        for violation in result.violations:
            lines.extend(
                [
                    "",
                    f"[{violation.rule_id}]",
                    "",
                    f"{violation.file}:{violation.line}:{violation.column}",
                    _format_source(
                        violation.matched_source, violation.column - 1
                    ),
                    "",
                    indent(violation.message, "    "),
                ]
            )

    lines.extend(
        [
            "",
            DIVIDER,
            f"Files scanned: {result.files_scanned}",
            f"Policies checked: {result.policies_checked}",
            f"Violations: {len(result.violations)}",
            "",
            "FAILED" if result.violations else "PASSED",
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
