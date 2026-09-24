"""Python parsing and pattern compilation using Tree-sitter."""

import re

import tree_sitter_python
from tree_sitter import Language, Parser

from codepatrol.errors import PolicyError
from codepatrol.languages.base import CompiledPattern, LanguageAdapter

METAVARIABLE = re.compile(r"\$([A-Z][A-Z0-9_]*)")
SENTINEL_PREFIX = "__CODEPATROL_META_"


class PythonAdapter(LanguageAdapter):
    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_python.language()))

    @property
    def name(self) -> str:
        return "python"

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".py",)

    def parse_source(self, source: bytes):
        return self._parser.parse(source)

    def compile_pattern(self, pattern: str) -> CompiledPattern:
        metavariables: dict[bytes, str] = {}

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            sentinel = f"{SENTINEL_PREFIX}{name}__"
            metavariables[sentinel.encode()] = name
            return sentinel

        transformed = METAVARIABLE.sub(replace, pattern)
        source = transformed.encode("utf-8")
        tree = self.parse_source(source)
        root = tree.root_node
        if root.has_error or len(root.named_children) != 1:
            raise PolicyError(f"Invalid Python pattern: {pattern!r}.")

        pattern_root = root.named_children[0]
        # Expression patterns should match the expression wherever it appears,
        # rather than requiring it to be a standalone statement.
        if pattern_root.type == "expression_statement":
            pattern_root = pattern_root.named_children[0]

        return CompiledPattern(
            root=pattern_root,
            source=source,
            metavariables=metavariables,
        )
