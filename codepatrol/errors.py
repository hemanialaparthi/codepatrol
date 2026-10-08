"""Exceptions that describe expected CodePatrol failures."""


class CodePatrolError(Exception):
    """Base class for errors that should be shown cleanly to CLI users."""


class PolicyError(CodePatrolError):
    """A policy file or policy pattern is invalid."""

    def __init__(self, message: str, *, policy_id: str | None = None) -> None:
        super().__init__(message)
        self.policy_id = policy_id


class ScanError(CodePatrolError):
    """A project could not be scanned safely."""
