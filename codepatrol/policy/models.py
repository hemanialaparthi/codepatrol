"""Small data models shared by policy-related modules."""

from dataclasses import dataclass

from codepatrol.languages.base import CompiledPattern


@dataclass(frozen=True)
class Policy:
    id: str
    language: str
    pattern: str
    message: str


@dataclass(frozen=True)
class CompiledPolicy:
    policy: Policy
    pattern: CompiledPattern

