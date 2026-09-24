"""Exceptions that describe expected CodePatrol failures."""


class CodePatrolError(Exception):
    """Base class for errors that should be shown cleanly to CLI users."""


class PolicyError(CodePatrolError):
    """A policy file or policy pattern is invalid."""


class ScanError(CodePatrolError):
    """A project could not be scanned safely."""

