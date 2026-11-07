"""Small data models shared by policy-related modules."""

from dataclasses import dataclass

from codepatrol.languages.base import CompiledPattern


@dataclass(frozen=True)
class CompiledXPath:
    """A compiled XPath 1.0 expression."""

    expression: object


@dataclass(frozen=True)
class CompiledFix:
    """Compiled deterministic replacement information."""

    replacement: str
    capture_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixSpec:
    """A developer-supplied deterministic replacement."""

    replacement: str


@dataclass(frozen=True)
class Policy:
    id: str
    language: str
    pattern: str | None
    message: str
    xpath: str | None = None
    fix: FixSpec | None = None


@dataclass(frozen=True)
class CompiledPolicy:
    policy: Policy
    pattern: CompiledPattern | None = None
    xpath: CompiledXPath | None = None
    fix: CompiledFix | None = None

