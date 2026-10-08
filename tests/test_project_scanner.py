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
