"""Language-neutral recursive structural matching."""

from collections.abc import Iterator
from typing import Any

from codepatrol.languages.base import CompiledPattern

NON_EXPRESSION_ARGUMENT_NODES = {
    "dictionary_splat",
    "keyword_argument",
    "list_splat",
    "slice",
}


def find_matches(
    pattern: CompiledPattern, source_root: Any, source: bytes
) -> Iterator[Any]:
    """Yield source nodes structurally equal to *pattern*, allowing wildcards."""
    for candidate in walk(source_root):
        if not candidate.is_named or candidate.type == "comment":
            continue
        captures: dict[str, Any] = {}
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
    captures: dict[str, Any],
) -> bool:
    pattern_text = _node_text(pattern_node, pattern.source)
    metavariable = pattern.metavariables.get(pattern_text)
    if metavariable is not None:
        if (
            not source_node.is_named
            or source_node.type == "comment"
            or source_node.type in NON_EXPRESSION_ARGUMENT_NODES
        ):
            return False
        previous = captures.get(metavariable)
        if previous is not None:
            return _structurally_equal(previous, source_node, source)
        captures[metavariable] = source_node
        return True

    if pattern_node.type != source_node.type:
        return False

    pattern_children = _significant_children(pattern_node)
    source_children = _significant_children(source_node)
    if len(pattern_children) != len(source_children):
        return False

    if not pattern_children:
        return pattern_text == _node_text(source_node, source)

    return all(
        left_field == right_field
        and _nodes_match(pattern, left, right, source, captures)
        for (left_field, left), (right_field, right) in zip(
            pattern_children, source_children, strict=True
        )
    )


def _node_text(node: Any, source: bytes) -> bytes:
    return source[node.start_byte : node.end_byte]


def _significant_children(node: Any) -> list[tuple[str | None, Any]]:
    """Return children with field names, excluding structurally inert comments."""
    return [
        (node.field_name_for_child(index), child)
        for index, child in enumerate(node.children)
        if child.type != "comment"
    ]


def _structurally_equal(left: Any, right: Any, source: bytes) -> bool:
    """Compare captured nodes with the same rules used by normal matching."""
    if left.type != right.type:
        return False
    left_children = _significant_children(left)
    right_children = _significant_children(right)
    if len(left_children) != len(right_children):
        return False
    if not left_children:
        return _node_text(left, source) == _node_text(right, source)
    return all(
        left_field == right_field
        and _structurally_equal(left_child, right_child, source)
        for (left_field, left_child), (right_field, right_child) in zip(
            left_children, right_children, strict=True
        )
    )
