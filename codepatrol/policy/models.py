"""Small data models shared by policy-related modules."""

from dataclasses import dataclass

from codepatrol.languages.base import CompiledPattern


@dataclass(frozen=True)
class CompiledXPath:
    """A compiled XPath 1.0 expression."""

    expression: object


@dataclass(frozen=True)
class Policy:
    id: str
    language: str
    pattern: str | None
    message: str
    xpath: str | None = None


@dataclass(frozen=True)
class CompiledPolicy:
    policy: Policy
    pattern: CompiledPattern | None = None
    xpath: CompiledXPath | None = None

