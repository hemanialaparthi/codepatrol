"""Python parsing and pattern compilation using Tree-sitter."""

import io
import re
import tokenize
from collections.abc import Iterator

import tree_sitter_python
from tree_sitter import Language, Parser

from codepatrol.errors import PolicyError
from codepatrol.languages.base import CompiledPattern, LanguageAdapter

METAVARIABLE = re.compile(r"\$([A-Z][A-Z0-9_]*)")
SENTINEL_PREFIX = "__CODEPATROL_META_"
IGNORED_PATTERN_NODE_TYPES = {"comment"}


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
        """Compile one structural pattern without rewriting strings or comments."""
        metavariables: dict[bytes, str] = {}
        protected_ranges, identifier_tokens = _token_ranges(pattern)
        for token_text, _, _ in identifier_tokens:
            if token_text.startswith(SENTINEL_PREFIX):
                raise PolicyError(
                    f"Identifier {token_text!r} uses CodePatrol's reserved "
                    f"prefix {SENTINEL_PREFIX!r}."
                )

        replacements: list[tuple[int, int, str]] = []
        index = 0
        while index < len(pattern):
            if pattern[index] != "$" or _is_protected(index, protected_ranges):
                index += 1
                continue

            match = METAVARIABLE.match(pattern, index)
            previous = pattern[index - 1] if index else ""
            if (
                match is None
                or _is_identifier_character(previous)
                or _is_identifier_character(
                    pattern[match.end()] if match.end() < len(pattern) else ""
                )
            ):
                excerpt = pattern[index : index + 20].splitlines()[0]
                raise PolicyError(
                    "Malformed metavariable at character "
                    f"{index + 1}: {excerpt!r}; expected $NAME using uppercase "
                    "letters, digits, or underscores."
                )

            name = match.group(1)
            sentinel = f"{SENTINEL_PREFIX}{name}__"
            metavariables[sentinel.encode("utf-8")] = name
            replacements.append((match.start(), match.end(), sentinel))
            index = match.end()

        transformed = pattern
        for start, end, replacement in reversed(replacements):
            transformed = transformed[:start] + replacement + transformed[end:]
        source = transformed.encode("utf-8")
        tree = self.parse_source(source)
        root = tree.root_node
        syntax_issues = list(_syntax_issues(root))
        if root.has_error and not syntax_issues:
            raise PolicyError(
                f"Invalid Python pattern (unlocated syntax error): {pattern!r}."
            )
        if syntax_issues:
            issue = syntax_issues[0]
            line = issue.start_point.row + 1
            column = _character_column(source, issue.start_point.row, issue.start_point.column)
            kind = "missing syntax" if issue.is_missing else "syntax error"
            raise PolicyError(
                f"Invalid Python pattern at {line}:{column} ({kind}): {pattern!r}."
            )

        meaningful_children = [
            child
            for child in root.named_children
            if child.type not in IGNORED_PATTERN_NODE_TYPES
        ]
        if len(meaningful_children) != 1:
            raise PolicyError(
                "Unsupported Python pattern shape: expected exactly one statement "
                f"or expression, got {len(meaningful_children)}."
            )

        pattern_root = meaningful_children[0]
        # Expression patterns should match the expression wherever it appears,
        # rather than requiring it to be a standalone statement.
        if pattern_root.type == "expression_statement":
            expression_children = [
                child
                for child in pattern_root.named_children
                if child.type not in IGNORED_PATTERN_NODE_TYPES
            ]
            if len(expression_children) != 1:
                raise PolicyError(
                    "Unsupported Python expression pattern shape: expected one "
                    "expression."
                )
            pattern_root = expression_children[0]

        if _node_text(pattern_root, source) in metavariables:
            raise PolicyError(
                "Unsupported Python pattern shape: a bare metavariable has no "
                "literal structure to anchor a match."
            )

        return CompiledPattern(
            root=pattern_root,
            source=source,
            metavariables=metavariables,
        )


def _token_ranges(
    source: str,
) -> tuple[list[tuple[int, int]], list[tuple[str, int, int]]]:
    """Return protected string/comment spans and identifier token spans."""
    line_offsets = _line_offsets(source)
    protected: list[tuple[int, int]] = []
    identifiers: list[tuple[str, int, int]] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    try:
        for token in tokens:
            start = _absolute_offset(line_offsets, source, token.start)
            end = _absolute_offset(line_offsets, source, token.end)
            if token.type in {tokenize.STRING, tokenize.COMMENT}:
                protected.append((start, end))
            elif token.type == tokenize.NAME:
                identifiers.append((token.string, start, end))
    except (IndentationError, tokenize.TokenError):
        # Tree-sitter below provides the authoritative, location-aware syntax
        # error. Tokens obtained before the failure are still useful here.
        pass
    return protected, identifiers


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", source):
        offsets.append(match.end())
    return offsets


def _absolute_offset(
    line_offsets: list[int], source: str, point: tuple[int, int]
) -> int:
    row, column = point
    if row <= 0:
        return 0
    if row > len(line_offsets):
        return len(source)
    return min(line_offsets[row - 1] + column, len(source))


def _is_protected(index: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in ranges)


def _is_identifier_character(character: str) -> bool:
    return bool(character) and (character == "_" or character.isalnum())


def _syntax_issues(root: object) -> Iterator[object]:
    for node in _walk(root):
        if node.is_error or node.is_missing:
            yield node


def _walk(node: object) -> Iterator[object]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _character_column(source: bytes, row: int, byte_column: int) -> int:
    lines = source.splitlines(keepends=True)
    line = lines[row] if row < len(lines) else b""
    return len(line[:byte_column].decode("utf-8", errors="replace")) + 1


def _node_text(node: object, source: bytes) -> bytes:
    return source[node.start_byte : node.end_byte]
