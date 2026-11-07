"""Tree-sitter to XML conversion and restricted XPath evaluation."""

from dataclasses import dataclass
from typing import Any

from lxml import etree

from codepatrol.policy.models import CompiledXPath


class XPathConversionError(ValueError):
    """Source text cannot be represented in the XML 1.0 XPath document."""

    def __init__(self, node: Any, character: str, byte_offset: int) -> None:
        self.node = node
        self.character = character
        self.byte_offset = byte_offset
        self.byte_end = byte_offset + len(character.encode("utf-8"))
        super().__init__(
            f"source text contains XML 1.0-incompatible Unicode code point "
            f"U+{ord(character):04X}."
        )


@dataclass
class XPathDocument:
    root: etree._Element
    nodes: dict[str, Any]


def build_document(tree_root: Any, source: bytes) -> XPathDocument:
    """Build the minimal local XML view used by XPath policies.

    Every named Tree-sitter node becomes a ``node`` element. Punctuation and
    other unnamed nodes are omitted. Only leaf nodes receive ``text``. Text
    containing XML 1.0-invalid characters raises ``XPathConversionError``;
    callers must reject the complete XPath evaluation for that source file.
    """
    nodes: dict[str, Any] = {}
    counter = 0

    def convert(node: Any, field: str | None = None) -> etree._Element | None:
        nonlocal counter
        if not node.is_named:
            return None
        node_id = f"n{counter}"
        counter += 1
        attributes = {"type": node.type, "id": node_id}
        if field is not None:
            attributes["field"] = field
        if not node.named_children:
            text = source[node.start_byte : node.end_byte].decode("utf-8")
            for index, character in enumerate(text):
                if not _is_xml_10_text(character):
                    byte_offset = node.start_byte + len(text[:index].encode("utf-8"))
                    raise XPathConversionError(node, character, byte_offset)
            attributes["text"] = text
        element = etree.Element("node", attrib=attributes)
        nodes[node_id] = node
        for index, child in enumerate(node.children):
            child_element = convert(child, node.field_name_for_child(index))
            if child_element is not None:
                element.append(child_element)
        return element

    root = convert(tree_root)
    assert root is not None
    return XPathDocument(root=root, nodes=nodes)


def evaluate_xpath(
    compiled: CompiledXPath, document: XPathDocument, policy_id: str
) -> list[Any]:
    """Return source nodes selected by an XPath 1.0 node-set expression."""
    result = compiled.expression(document.root)
    if not isinstance(result, list):
        raise ValueError(
            f"Rule '{policy_id}' XPath must return source-associated elements; "
            f"got {type(result).__name__}."
        )
    selected: list[Any] = []
    seen: set[str] = set()
    reverse = {id(element): node_id for element, node_id in _elements(document.root)}
    for element in result:
        if not isinstance(element, etree._Element) or id(element) not in reverse:
            raise ValueError(
                f"Rule '{policy_id}' XPath returned an unsupported or non-source "
                "element result. Select source-associated node elements only."
            )
        node_id = reverse[id(element)]
        if node_id not in seen:
            seen.add(node_id)
            selected.append(document.nodes[node_id])
    selected.sort(key=lambda node: (node.start_byte, node.end_byte))
    return selected


def _elements(root: etree._Element):
    for element in root.iter("node"):
        node_id = element.get("id")
        assert node_id is not None
        yield element, node_id


def _is_xml_10_text(text: str) -> bool:
    return all(
        character in "\t\n\r"
        or "\u0020" <= character <= "\uD7FF"
        or "\uE000" <= character <= "\uFFFD"
        for character in text
    )
