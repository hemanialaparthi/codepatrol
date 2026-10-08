import pytest

from codepatrol.errors import PolicyError
from codepatrol.languages.python import PythonAdapter
from codepatrol.scanner.matcher import find_matches


def matches(pattern_text: str, source_text: str) -> bool:
    adapter = PythonAdapter()
    pattern = adapter.compile_pattern(pattern_text)
    source = source_text.encode()
    tree = adapter.parse_source(source)
    return bool(list(find_matches(pattern, tree.root_node, source)))


def test_metavariable_is_recorded():
    pattern = PythonAdapter().compile_pattern("print($MSG)")

    assert set(pattern.metavariables.values()) == {"MSG"}


def test_invalid_python_pattern_fails_cleanly():
    with pytest.raises(PolicyError, match="Invalid Python pattern"):
        PythonAdapter().compile_pattern("print(")


@pytest.mark.parametrize("pattern", ["print($lower)", "print($)", "print($1)", "x$X"])
def test_malformed_metavariables_are_rejected(pattern):
    with pytest.raises(PolicyError, match="Malformed metavariable"):
        PythonAdapter().compile_pattern(pattern)


def test_reserved_sentinel_identifier_is_rejected():
    with pytest.raises(PolicyError, match="reserved prefix"):
        PythonAdapter().compile_pattern("__CODEPATROL_META_X__()")


def test_bare_metavariable_is_an_unsupported_unanchored_pattern():
    with pytest.raises(PolicyError, match="bare metavariable"):
        PythonAdapter().compile_pattern("$X")


def test_metavariable_text_in_strings_and_comments_is_literal():
    pattern = PythonAdapter().compile_pattern('print("$lower")  # $also_literal')

    assert pattern.metavariables == {}
    assert matches('print("$lower")', 'print("$lower")')
    assert not matches('print("$lower")', 'print("different")')


@pytest.mark.parametrize(
    "source",
    [
        'print("hello")',
        'print   (   "hello"   )',
        'print(\n    "hello"\n)',
        'print("hello " + username)',
        'value = print(calculate_total(price, tax))',
    ],
)
def test_print_pattern_matches_one_argument_calls(source):
    assert matches("print($MSG)", source)


@pytest.mark.parametrize(
    "source",
    [
        'logger.info("hello")',
        'printf("hello")',
        'print("hello", username)',
    ],
)
def test_print_pattern_rejects_different_structures(source):
    assert not matches("print($MSG)", source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("a + a", True),
        ("a + b", False),
        ("(a * b) + (a * b)", True),
        ("(a * b) + (a / b)", False),
        ("(a  # comment\n * b) + (a * b)", True),
    ],
)
def test_repeated_metavariables_use_structural_equivalence(source, expected):
    assert matches("$X + $X", source) is expected


def test_failed_repeated_binding_does_not_leak_to_later_candidate():
    adapter = PythonAdapter()
    pattern = adapter.compile_pattern("$X + $X")
    source = b"a + b\nc + c\n"
    tree = adapter.parse_source(source)

    found = list(find_matches(pattern, tree.root_node, source))

    assert [source[node.start_byte : node.end_byte] for node in found] == [b"c + c"]


@pytest.mark.parametrize(
    "pattern,source",
    [
        ("left + right", "left - right"),
        ("call(1)", "call(2)"),
        ("call(name)", "call(other)"),
        ("call($X)", "call(value=1)"),
    ],
)
def test_literals_operators_and_argument_roles_are_preserved(pattern, source):
    assert not matches(pattern, source)
