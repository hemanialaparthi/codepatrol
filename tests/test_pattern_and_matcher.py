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

