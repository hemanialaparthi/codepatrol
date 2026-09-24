"""Language-neutral recursive structural matching."""

from collections.abc import Iterator
from typing import Any

from codepatrol.languages.base import CompiledPattern


def find_matches(
    pattern: CompiledPattern, source_root: Any, source: bytes
) -> Iterator[Any]:
    """Yield source nodes structurally equal to *pattern*, allowing wildcards."""
    for candidate in walk(source_root):
        captures: dict[str, bytes] = {}
        if _nodes_match(pattern, pattern.root, candidate, source, captures):
            yield candidate


def walk(node: Any) -> Iterator[Any]:
    yield node
    for child in node.children:
        yield from walk(child)


def _nodes_match(
    pattern: CompiledPattern,
    pattern_node: Any,
    source_node: Any,
    source: bytes,
    captures: dict[str, bytes],
) -> bool:
    pattern_text = _node_text(pattern_node, pattern.source)
    metavariable = pattern.metavariables.get(pattern_text)
    if metavariable is not None:
        captured_text = _node_text(source_node, source)
        previous = captures.get(metavariable)
        if previous is not None and previous != captured_text:
            return False
        captures[metavariable] = captured_text
        return True

    if pattern_node.type != source_node.type:
        return False

    pattern_children = pattern_node.children
    source_children = source_node.children
    if len(pattern_children) != len(source_children):
        return False

    if not pattern_children:
        return pattern_text == _node_text(source_node, source)

    return all(
        _nodes_match(pattern, left, right, source, captures)
        for left, right in zip(pattern_children, source_children, strict=True)
    )


def _node_text(node: Any, source: bytes) -> bytes:
    return source[node.start_byte : node.end_byte]

