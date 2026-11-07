import json
import shlex
import sys

from codepatrol.cli import main
from codepatrol.languages import default_registry
from codepatrol.policy.compiler import compile_policies
from codepatrol.policy.loader import load_policies


def write_policy(tmp_path, text):
    path = tmp_path / "policies.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_pattern_fix_substitutes_original_capture(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("legacy_send(  value )\n", encoding="utf-8")
    policy = write_policy(tmp_path, """rules:
  - id: replace-legacy-send
    language: python
    pattern: legacy_send($VALUE)
    message: Use modern_send.
    fix:
      replacement: modern_send($VALUE)
""")

    assert main(["scan", str(project), "--policies", str(policy), "--fix"]) == 0
    assert source.read_text(encoding="utf-8") == "modern_send(value)\n"


def test_xpath_fix_replaces_only_selected_callee(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("legacy_send(value)\nobj.legacy_send(value)\n", encoding="utf-8")
    policy = write_policy(tmp_path, """rules:
  - id: rename-legacy-send
    language: python
    xpath: >-
      //node[@type='call']
        /node[@field='function' and @type='identifier' and @text='legacy_send']
    message: Rename the direct call.
    fix:
      replacement: modern_send
""")

    assert main(["scan", str(project), "--policies", str(policy), "--fix"]) == 0
    assert source.read_text(encoding="utf-8") == "modern_send(value)\nobj.legacy_send(value)\n"


def test_fix_tests_import_staged_source(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text(
        "def legacy_send(value):\n    return 'legacy'\n"
        "def modern_send(value):\n    return 'modern'\n"
        "RESULT = legacy_send(1)\n",
        encoding="utf-8",
    )
    policy = write_policy(tmp_path, """rules:
  - id: replace-call
    language: python
    pattern: legacy_send($VALUE)
    message: Replace call.
    fix:
      replacement: modern_send($VALUE)
""")
    command = (
        f"{shlex.quote(sys.executable)} -c "
        + shlex.quote("import app; assert app.RESULT == 'modern'")
    )

    code = main(["scan", str(project), "--policies", str(policy), "--fix",
                 "--test-command", command, "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 0, json.dumps(report, indent=2)
    assert report["tests"] == {"configured": True, "ran": True}
    assert report["validation_location"].startswith("isolated staged project")
    assert report["source_preserved"] is False
    assert source.read_text(encoding="utf-8").endswith("RESULT = modern_send(1)\n")


def test_rejected_candidate_preserves_original_source(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    original = "legacy_send(value)\n"
    source.write_text(original, encoding="utf-8")
    policy = write_policy(tmp_path, """rules:
  - id: invalid-fix
    language: python
    pattern: legacy_send($VALUE)
    message: Invalid replacement.
    fix:
      replacement: "modern_send("
""")

    assert main(["scan", str(project), "--policies", str(policy), "--fix"]) == 2
    assert source.read_text(encoding="utf-8") == original


def test_noop_fix_is_not_claimed_as_applied(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("legacy_send(value)\n", encoding="utf-8")
    policy = write_policy(tmp_path, """rules:
  - id: noop
    language: python
    pattern: legacy_send($VALUE)
    message: Already equivalent.
    fix:
      replacement: legacy_send($VALUE)
""")

    code = main(["scan", str(project), "--policies", str(policy), "--fix", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["edits"] == {"proposed": 1, "applied": 0}
    assert report["source_preserved"] is True


def test_passing_policy_still_runs_configured_baseline_tests(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    policy = write_policy(tmp_path, """rules:
  - id: no-print
    language: python
    pattern: print($VALUE)
    message: No print.
""")
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote("raise SystemExit(0)")

    code = main(["scan", str(project), "--policies", str(policy), "--fix",
                 "--test-command", command, "--json"])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["initial"]["status"] == "PASS"
    assert report["tests"] == {"configured": True, "ran": True}
    assert report["edits"] == {"proposed": 0, "applied": 0}


def test_fix_configuration_rejects_unknown_fields_and_keeps_xpath_literal(tmp_path):
    unknown = write_policy(tmp_path, """rules:
  - id: bad
    language: python
    pattern: print($X)
    message: bad
    fix:
      replacement: print($X)
      extra: nope
""")
    try:
        load_policies(unknown, default_registry())
    except Exception as exc:
        assert "unknown field" in str(exc)
    else:
        raise AssertionError("unknown fix fields were accepted")

    xpath = write_policy(tmp_path, """rules:
  - id: literal-xpath-fix
    language: python
    xpath: //node[@type='identifier']
    message: bad
    fix:
      replacement: $X
""")
    compiled = compile_policies(load_policies(xpath, default_registry()), default_registry())
    assert compiled[0].fix is not None
    assert compiled[0].fix.replacement == "$X"
