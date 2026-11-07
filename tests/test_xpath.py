from lxml import etree

from codepatrol.languages.python import PythonAdapter
from codepatrol.policy.models import CompiledXPath
from codepatrol.scanner.xpath import build_document, evaluate_xpath


def test_xml_schema_is_minimal_and_uses_named_node_hierarchy():
    source = b"x = print(1)\n"
    tree = PythonAdapter().parse_source(source)

    document = build_document(tree.root_node, source)
    root = document.root

    assert set(root.attrib) == {"type", "id"}
    assert all(set(element.attrib) <= {"type", "field", "text", "id"} for element in root.iter())
    assert all(element.tag == "node" for element in root.iter())
    assert any(element.get("field") == "function" for element in root.iter())
    assert any(element.get("text") == "print" for element in root.iter())


def test_xpath_fixture_query_selects_the_documented_source_node():
    source = (
        b"def calculate_total(value):\n"
        b"    print(value)\n"
        b"    return value\n"
    )
    tree = PythonAdapter().parse_source(source)
    document = build_document(tree.root_node, source)
    expression = etree.XPath(
        "//node[@type='function_definition']"
        "[node[@field='name' and @type='identifier' and @text='calculate_total']]"
        "//node[@type='call']"
        "[node[@field='function' and @type='identifier' and @text='print']]"
    )

    matches = evaluate_xpath(
        CompiledXPath(expression), document, "no-print-in-calculate-total"
    )

    assert len(matches) == 1
    assert source[matches[0].start_byte : matches[0].end_byte] == b"print(value)"


def test_valid_unicode_and_xml_special_text_remain_queryable():
    source = 'value = "é & <"\n'.encode("utf-8")
    tree = PythonAdapter().parse_source(source)
    document = build_document(tree.root_node, source)
    expression = etree.XPath(
        "//node[@type='string_content' and @text='é & <']"
    )

    matches = evaluate_xpath(
        CompiledXPath(expression), document, "special-string"
    )

    assert len(matches) == 1
    string_element = next(
        element
        for element in document.root.iter()
        if element.get("type") == "string_content"
    )
    assert string_element.get("text") == "é & <"
