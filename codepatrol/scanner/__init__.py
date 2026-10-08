"""Structural matching and recursive project scanning."""

from codepatrol.scanner.models import CheckError, ScanResult, SourceLocation, Violation
from codepatrol.scanner.project import ProjectScanner

__all__ = [
    "CheckError",
    "ProjectScanner",
    "ScanResult",
    "SourceLocation",
    "Violation",
]
