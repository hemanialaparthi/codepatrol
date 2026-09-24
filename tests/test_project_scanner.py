from pathlib import Path

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

