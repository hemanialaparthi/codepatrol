"""The deliberately small boundary between the core and a language grammar."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompiledPattern:
    root: Any
    source: bytes
    metavariables: dict[bytes, str]


class LanguageAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical policy name for the language."""

    @property
    @abstractmethod
    def extensions(self) -> tuple[str, ...]:
        """File extensions handled by this adapter, including leading dots."""

    @abstractmethod
    def parse_source(self, source: bytes) -> Any:
        """Return a Tree-sitter-compatible syntax tree."""

    @abstractmethod
    def compile_pattern(self, pattern: str) -> CompiledPattern:
        """Parse a user pattern and record any metavariable sentinels."""

