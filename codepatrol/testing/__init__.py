"""Framework-independent functional test execution."""

from codepatrol.testing.models import (
    ExecutionError,
    FrameworkDetails,
    FrameworkResultAdapter,
    TestCommandResult,
    TestOutcome,
)
from codepatrol.testing.runner import parse_command, run_test_command

__all__ = [
    "ExecutionError",
    "FrameworkDetails",
    "FrameworkResultAdapter",
    "TestCommandResult",
    "TestOutcome",
    "parse_command",
    "run_test_command",
]
