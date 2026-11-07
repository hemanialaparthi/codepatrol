from pathlib import Path

import pytest

from codepatrol.languages import (
    CompiledPattern,
    LanguageAdapter,
    LanguageRegistry,
    PythonAdapter,
    default_registry,
)
from codepatrol.policy.compiler import compile_policies
from codepatrol.policy.models import Policy
from codepatrol.scanner import ProjectScanner


def compile_rules(registry, *patterns):
    policies = [
        Policy(
            id=f"rule-{index}",
            language="python",
            pattern=pattern,
            message=f"Found {pattern}",
        )
        for index, pattern in enumerate(patterns)
    ]
    return compile_policies(policies, registry)


def compile_xpath_rule(registry, expression, *, rule_id="xpath-rule"):
    policy = Policy(
        id=rule_id,
        language="python",
        pattern=None,
        message="Found by XPath.",
        xpath=expression,
    )
    return compile_policies([policy], registry)


def test_scan_recurses_ignores_directories_and_reports_locations(tmp_path):
    project = tmp_path / "project"
    nested = project / "src" / "nested"
    nested.mkdir(parents=True)
    (nested / "app.py").write_text('\nprint("hello")\n', encoding="utf-8")
    (project / "clean.py").write_text("x = 1\n", encoding="utf-8")
    ignored = project / ".venv"
    ignored.mkdir()
    (ignored / "ignored.py").write_text('print("ignored")\n', encoding="utf-8")
    (project / "notes.txt").write_text('print("not Python")\n', encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project, compile_rules(registry, "print($MSG)")
    )

    assert result.files_scanned == 2
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.file == nested / "app.py"
    assert (violation.line, violation.column) == (2, 1)


def test_scan_applies_multiple_policies(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        'print("hello")\nsize = len(items)\n', encoding="utf-8"
    )
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project, compile_rules(registry, "print($X)", "len($X)")
    )

    assert result.policies_checked == 2
    assert len(result.violations) == 2


def test_pattern_and_xpath_policies_are_evaluated_together(tmp_path):
    source_file = tmp_path / "mixed.py"
    source_file.write_text("print(value)\nsize = len(items)\n", encoding="utf-8")
    registry = default_registry()
    policies = compile_rules(registry, "print($X)") + compile_xpath_rule(
        registry,
        "//node[@type='call'][node[@field='function' and @text='len']]",
        rule_id="no-len",
    )

    result = ProjectScanner(registry).scan(source_file, policies)

    assert {item.rule_id for item in result.violations} == {"rule-0", "no-len"}


def test_xpath_policy_maps_selected_call_to_existing_violation(tmp_path):
    source_file = tmp_path / "calculate.py"
    source_file.write_text(
        "def calculate_total(value):\n    print(value)\n    return value\n",
        encoding="utf-8",
    )
    registry = default_registry()
    rules = compile_xpath_rule(
        registry,
        "//node[@type='function_definition']"
        "[node[@field='name' and @type='identifier' and @text='calculate_total']]"
        "//node[@type='call']"
        "[node[@field='function' and @type='identifier' and @text='print']]",
        rule_id="no-print-in-total",
    )

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "FAIL"
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert (violation.line, violation.column) == (2, 5)
    assert violation.matched_source == "print(value)"


def test_xpath_empty_node_set_is_a_clean_policy_result(tmp_path):
    source_file = tmp_path / "clean.py"
    source_file.write_text("value = 1\n", encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        source_file, compile_xpath_rule(registry, "//node[@type='call']")
    )

    assert result.status == "PASS"
    assert result.violations == []


def test_xpath_xml_invalid_source_text_is_a_location_aware_checking_error(tmp_path):
    source_file = tmp_path / "invalid_xml_text.py"
    source_file.write_bytes(b"print(value)  # \x01\n")
    registry = default_registry()
    rules = compile_xpath_rule(
        registry,
        "//node[@type='call'][node[@field='function' and @text='print']]",
        rule_id="xml-text",
    )

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "ERROR"
    assert result.violations == []
    error = result.errors[0]
    assert error.category == "checking"
    assert error.file == source_file
    assert error.policy_id is None
    assert (error.start.line, error.start.column) == (1, 17)
    assert (error.end.line, error.end.column) == (1, 18)
    assert "U+0001" in error.message


def test_pattern_only_scan_remains_independent_of_xml_conversion(tmp_path):
    source_file = tmp_path / "pattern_only.py"
    source_file.write_bytes(b"print(value)  # \x01\n")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        source_file, compile_rules(registry, "print($X)")
    )

    assert result.status == "FAIL"
    assert result.errors == []
    assert result.violations[0].matched_source == "print(value)"


def test_mixed_scan_keeps_pattern_findings_when_xpath_conversion_fails(tmp_path):
    source_file = tmp_path / "mixed_invalid_xml.py"
    source_file.write_bytes(b"print(value)  # \x01\n")
    registry = default_registry()
    policies = compile_rules(registry, "print($X)") + compile_xpath_rule(
        registry,
        "//node[@type='call'][node[@field='function' and @text='print']]",
        rule_id="xpath-print",
    )

    result = ProjectScanner(registry).scan(source_file, policies)

    assert result.status == "ERROR"
    assert [item.rule_id for item in result.violations] == ["rule-0"]
    assert result.errors[0].category == "checking"


def test_literal_backslash_escape_sequence_is_not_an_invalid_character(tmp_path):
    source_file = tmp_path / "literal_escape.py"
    source_file.write_text("value = r'\\x01'\n", encoding="utf-8")
    registry = default_registry()
    rules = compile_xpath_rule(registry, "//node[@type='string']")

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "FAIL"
    assert result.errors == []
    assert result.violations[0].matched_source == "r'\\x01'"


def test_xpath_distinguishes_direct_and_attribute_calls(tmp_path):
    source_file = tmp_path / "calls.py"
    source_file.write_text("print(value)\nlogger.print(value)\n", encoding="utf-8")
    registry = default_registry()
    rules = compile_xpath_rule(
        registry,
        "//node[@type='call'][node[@field='function' and @type='identifier' and @text='print']]",
    )

    result = ProjectScanner(registry).scan(source_file, rules)

    assert [item.matched_source for item in result.violations] == ["print(value)"]


def test_xpath_ignores_similar_comments_and_strings(tmp_path):
    source_file = tmp_path / "text.py"
    source_file.write_text(
        '# print(value)\ntext = "print(value)"\nprint(value)\n', encoding="utf-8"
    )
    registry = default_registry()
    rules = compile_xpath_rule(
        registry,
        "//node[@type='call'][node[@field='function' and @type='identifier' and @text='print']]",
    )

    result = ProjectScanner(registry).scan(source_file, rules)

    assert [item.line for item in result.violations] == [3]


def test_xpath_preserves_nested_function_descendant_semantics(tmp_path):
    source_file = tmp_path / "nested.py"
    source_file.write_text(
        "def calculate_total(value):\n"
        "    def nested():\n"
        "        print(value)\n"
        "    print(value)\n",
        encoding="utf-8",
    )
    registry = default_registry()
    rules = compile_xpath_rule(
        registry,
        "//node[@type='function_definition']"
        "[node[@field='name' and @text='calculate_total']]"
        "//node[@type='call'][node[@field='function' and @text='print']]",
    )

    result = ProjectScanner(registry).scan(source_file, rules)

    assert [item.line for item in result.violations] == [3, 4]


def test_xpath_rejects_scalar_results_as_checking_error(tmp_path):
    source_file = tmp_path / "source.py"
    source_file.write_text("value = 1\n", encoding="utf-8")
    registry = default_registry()
    rules = compile_xpath_rule(registry, "count(//node)", rule_id="scalar")

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "ERROR"
    assert result.errors[0].policy_id == "scalar"
    assert "source-associated elements" in result.errors[0].message


def test_xpath_rejects_attribute_results_as_checking_error(tmp_path):
    source_file = tmp_path / "source.py"
    source_file.write_text("value = 1\n", encoding="utf-8")
    registry = default_registry()
    rules = compile_xpath_rule(registry, "//node/@type", rule_id="attribute")

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "ERROR"
    assert result.errors[0].policy_id == "attribute"


def test_xpath_invalid_source_has_existing_parse_error_semantics(tmp_path):
    source_file = tmp_path / "broken.py"
    source_file.write_text("def broken(:\n    pass\n", encoding="utf-8")
    registry = default_registry()
    rules = compile_xpath_rule(registry, "//node[@type='call']")

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "ERROR"
    assert result.errors[0].category == "parsing"


def test_xpath_unicode_locations_use_existing_character_columns(tmp_path):
    source_file = tmp_path / "unicode.py"
    source_file.write_text('é = 1; print("x")\n', encoding="utf-8")
    registry = default_registry()
    rules = compile_xpath_rule(
        registry,
        "//node[@type='call'][node[@field='function' and @text='print']]",
    )

    result = ProjectScanner(registry).scan(source_file, rules)

    finding = result.violations[0]
    assert (finding.line, finding.column, finding.end_column) == (1, 8, 18)


class FakeLanguageAdapter(LanguageAdapter):
    """A test adapter with a new extension and Python-like test grammar."""

    def __init__(self):
        self.delegate = PythonAdapter()

    @property
    def name(self) -> str:
        return "fake"

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".fake",)

    def parse_source(self, source: bytes):
        return self.delegate.parse_source(source)

    def compile_pattern(self, pattern: str) -> CompiledPattern:
        return self.delegate.compile_pattern(pattern)


def test_scanner_uses_adapter_extension_without_python_coupling(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "example.fake").write_text("flagged(value)\n", encoding="utf-8")
    registry = LanguageRegistry()
    registry.register(FakeLanguageAdapter())
    policy = Policy("fake-rule", "fake", "flagged($X)", "Found it.")

    result = ProjectScanner(registry).scan(
        project, compile_policies([policy], registry)
    )

    assert result.files_scanned == 1
    assert len(result.violations) == 1


def test_policy_pattern_changes_behavior_without_code_changes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        'print("hello")\nsize = len(items)\n', encoding="utf-8"
    )
    registry = default_registry()
    scanner = ProjectScanner(registry)

    print_result = scanner.scan(project, compile_rules(registry, "print($X)"))
    len_result = scanner.scan(project, compile_rules(registry, "len($X)"))

    assert [item.matched_source for item in print_result.violations] == [
        'print("hello")'
    ]
    assert [item.matched_source for item in len_result.violations] == ["len(items)"]


def test_nested_source_tree_is_fully_traversed_without_duplicate_matches(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "nested.py").write_text(
        """class Service:
    async def run(self, items):
        return [print(item) for item in items if print(item)]
""",
        encoding="utf-8",
    )
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project, compile_rules(registry, "print($X)")
    )

    assert result.status == "FAIL"
    assert [item.matched_source for item in result.findings] == [
        "print(item)",
        "print(item)",
    ]


def test_mixed_valid_and_invalid_files_retains_reliable_findings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "good.py").write_text('print("found")\n', encoding="utf-8")
    (project / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project, compile_rules(registry, "print($X)")
    )

    assert result.status == "ERROR"
    assert result.files_selected == 2
    assert result.files_scanned == 1
    assert [item.matched_source for item in result.findings] == ['print("found")']
    assert result.errors[0].category == "parsing"
    assert result.errors[0].start is not None


def test_utf8_byte_columns_are_reported_as_character_columns(tmp_path):
    source_file = tmp_path / "unicode.py"
    source_file.write_text('é = 1; print("x")\n', encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        source_file, compile_rules(registry, "print($X)")
    )

    finding = result.findings[0]
    assert (finding.line, finding.column) == (1, 8)
    assert (finding.end_line, finding.end_column) == (1, 18)
    assert finding.matched_source == 'print("x")'


def test_overlapping_inputs_are_deduplicated_and_sorted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first = project / "a.py"
    second = project / "b.py"
    first.write_text("print(1)\n", encoding="utf-8")
    second.write_text("print(2)\n", encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        [second, project, first], compile_rules(registry, "print($X)")
    )

    assert result.files_selected == 2
    assert result.files_scanned == 2
    assert [item.file.name for item in result.findings] == ["a.py", "b.py"]


def test_default_and_custom_exclusions_are_counted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "keep.py").write_text("print(1)\n", encoding="utf-8")
    cache = project / "__pycache__"
    cache.mkdir()
    (cache / "cached.py").write_text("print(2)\n", encoding="utf-8")
    generated = project / "generated"
    generated.mkdir()
    (generated / "output.py").write_text("print(3)\n", encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project,
        compile_rules(registry, "print($X)"),
        exclusions=["generated"],
    )

    assert result.files_selected == 1
    assert result.files_excluded == 2
    assert [item.file.name for item in result.findings] == ["keep.py"]


def test_directory_symlink_is_not_followed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print(1)\n", encoding="utf-8")
    link = project / "loop"
    try:
        link.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project, compile_rules(registry, "print($X)")
    )

    assert result.files_scanned == 1
    assert len(result.findings) == 1


def test_empty_selection_is_an_error(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("print(1)\n", encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        project, compile_rules(registry, "print($X)")
    )

    assert result.status == "ERROR"
    assert result.files_selected == 0
    assert result.errors[0].category == "discovery"


def test_explicit_unsupported_file_is_reported(tmp_path):
    source_file = tmp_path / "notes.txt"
    source_file.write_text("print(1)\n", encoding="utf-8")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        source_file, compile_rules(registry, "print($X)")
    )

    assert result.status == "ERROR"
    assert any("Unsupported explicitly supplied file" in error.message for error in result.errors)


def test_non_utf8_file_is_a_decoding_error(tmp_path):
    source_file = tmp_path / "encoded.py"
    source_file.write_bytes(b"value = '\xff'\n")
    registry = default_registry()

    result = ProjectScanner(registry).scan(
        source_file, compile_rules(registry, "print($X)")
    )

    assert result.status == "ERROR"
    assert result.files_scanned == 0
    assert result.errors[0].category == "decoding"
    assert result.errors[0].start is not None


def test_unreadable_file_is_a_reading_error(tmp_path, monkeypatch):
    source_file = tmp_path / "unreadable.py"
    source_file.write_text("print(1)\n", encoding="utf-8")
    registry = default_registry()
    rules = compile_rules(registry, "print($X)")
    original_read_bytes = Path.read_bytes

    def fail_for_source(path):
        if path == source_file.resolve():
            raise PermissionError("permission denied for test")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_for_source)

    result = ProjectScanner(registry).scan(source_file, rules)

    assert result.status == "ERROR"
    assert result.files_scanned == 0
    assert result.errors[0].category == "reading"
