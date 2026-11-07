"""Bounded replacement-template compilation."""

import io
import re
import tokenize
from dataclasses import dataclass

from codepatrol.errors import PolicyError
from codepatrol.languages.base import CompiledPattern
from codepatrol.policy.models import CompiledFix

REFERENCE = re.compile(r"\$([A-Z][A-Z0-9_]*)")


@dataclass(frozen=True)
class ReplacementSegment:
    text: str
    capture: str | None = None


def compile_replacement(
    replacement: str, pattern: CompiledPattern | None
) -> CompiledFix:
    if pattern is None:
        # XPath replacements are fixed literals; dollar-like text is not a
        # pattern reference and is validated only after it is inserted.
        return CompiledFix(replacement=replacement)
    protected = _protected_ranges(replacement)
    names = set(pattern.metavariables.values())
    captures: list[str] = []
    for match in REFERENCE.finditer(replacement):
        if _contains(protected, match.start()):
            continue
        before = replacement[match.start() - 1] if match.start() else ""
        after = replacement[match.end()] if match.end() < len(replacement) else ""
        if _identifier_character(before) or _identifier_character(after):
            continue
        name = match.group(1)
        if name not in names:
            raise PolicyError(f"Replacement references unknown capture '${name}'.")
        captures.append(name)
    return CompiledFix(replacement=replacement, capture_names=tuple(captures))


def render_replacement(fix: CompiledFix, captures: dict[str, object], source: bytes) -> str:
    protected = _protected_ranges(fix.replacement)
    output: list[str] = []
    cursor = 0
    for match in REFERENCE.finditer(fix.replacement):
        if _contains(protected, match.start()):
            continue
        before = fix.replacement[match.start() - 1] if match.start() else ""
        after = fix.replacement[match.end()] if match.end() < len(fix.replacement) else ""
        if _identifier_character(before) or _identifier_character(after):
            continue
        node = captures[match.group(1)]
        output.append(fix.replacement[cursor : match.start()])
        output.append(source[node.start_byte : node.end_byte].decode("utf-8"))
        cursor = match.end()
    output.append(fix.replacement[cursor:])
    return "".join(output)


def _protected_ranges(text: str) -> list[tuple[int, int]]:
    offsets = _line_offsets(text)
    ranges: list[tuple[int, int]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type in {tokenize.STRING, tokenize.COMMENT}:
                start = offsets[token.start[0] - 1] + token.start[1]
                end = offsets[token.end[0] - 1] + token.end[1]
                ranges.append((start, end))
            if tokenize.tok_name[token.type].startswith("FSTRING"):
                raise PolicyError("F-string replacement templates are unsupported.")
    except tokenize.TokenError:
        # Incomplete replacement templates are intentionally accepted here;
        # the complete edited source is validated after substitution.
        pass
    return ranges


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(text):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _contains(ranges: list[tuple[int, int]], position: int) -> bool:
    return any(start <= position < end for start, end in ranges)


def _identifier_character(character: str) -> bool:
    return character == "_" or character.isalnum()
