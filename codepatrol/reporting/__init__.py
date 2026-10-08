"""Human- and machine-readable report formats."""

from codepatrol.reporting.json import render_json_report
from codepatrol.reporting.terminal import render_terminal_report

__all__ = ["render_json_report", "render_terminal_report"]
