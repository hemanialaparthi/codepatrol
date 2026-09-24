"""Lookup table for installed language adapters."""

from codepatrol.errors import PolicyError
from codepatrol.languages.base import LanguageAdapter


class LanguageRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, LanguageAdapter] = {}

    def register(self, adapter: LanguageAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"Language '{adapter.name}' is already registered.")
        self._adapters[adapter.name] = adapter

    def supports(self, language: str) -> bool:
        return language in self._adapters

    def get(self, language: str) -> LanguageAdapter:
        try:
            return self._adapters[language]
        except KeyError as exc:
            raise PolicyError(f"Unsupported language: '{language}'.") from exc


def default_registry() -> LanguageRegistry:
    # Importing here keeps the registry reusable and avoids an import cycle.
    from codepatrol.languages.python import PythonAdapter

    registry = LanguageRegistry()
    registry.register(PythonAdapter())
    return registry

