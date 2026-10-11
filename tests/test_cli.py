import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from codepatrol.cli import cli, main

runner = CliRunner()


def test_cli_returns_one_for_violations(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text('print("hello")\n', encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text(
        """rules:
  - id: no-print
    language: python
    pattern: print($X)
    message: Use logging.
""",
        encoding="utf-8",
    )

    exit_code = main(["scan", str(project), "--policies", str(policies)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "Violations: 1" in output
    assert output.startswith("FAIL — 1 policy violation remains.")


def test_cli_returns_zero_for_clean_scan(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text(
        """rules:
  - id: no-print
    language: python
    pattern: print($X)
    message: Use logging.
""",
        encoding="utf-8",
    )

    exit_code = main(["scan", str(project), "--policies", str(policies)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Violations: 0" in output
    assert output.startswith("PASS — Policies passed with no violations.")


def test_cli_returns_two_for_bad_configuration(tmp_path, capsys):
    exit_code = main(
        ["scan", str(tmp_path / "missing"), "--policies", "missing.yaml"]
    )

    assert exit_code == 2
    assert "Project does not exist" in capsys.readouterr().out


def write_print_policy(tmp_path):
    policies = tmp_path / "policies.yaml"
    policies.write_text(
        """rules:
  - id: no-print
    language: python
    pattern: print($X)
    message: Use logging.
""",
        encoding="utf-8",
    )
    return policies


def test_json_reports_pass_fail_and_error_exit_codes(tmp_path, capsys):
    policies = write_print_policy(tmp_path)
    clean = tmp_path / "clean.py"
    failing = tmp_path / "failing.py"
    invalid = tmp_path / "invalid.py"
    clean.write_text("value = 1\n", encoding="utf-8")
    failing.write_text("print(value)\n", encoding="utf-8")
    invalid.write_text("def broken(:\n    pass\n", encoding="utf-8")

    for path, expected_status, expected_code in [
        (clean, "PASS", 0),
        (failing, "FAIL", 1),
        (invalid, "ERROR", 2),
    ]:
        exit_code = main(
            ["scan", str(path), "--policies", str(policies), "--json"]
        )
        captured = capsys.readouterr()
        report = json.loads(captured.out)
        assert exit_code == expected_code
        assert report["status"] == expected_status
        assert report["schema_version"] == "1.0"
        assert captured.err == ""


def test_json_error_is_structured_and_does_not_emit_traceback(tmp_path, capsys):
    policies = tmp_path / "bad.yaml"
    policies.write_text("rules: [unterminated", encoding="utf-8")
    source = tmp_path / "source.py"
    source.write_text("pass\n", encoding="utf-8")

    exit_code = main(
        ["scan", str(source), "--policies", str(policies), "--json"]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert report["status"] == "ERROR"
    assert report["errors"][0]["category"] == "configuration"
    assert "Traceback" not in captured.out + captured.err


def test_json_output_is_identical_across_repeated_runs(tmp_path, capsys):
    policies = write_print_policy(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "b.py").write_text("print(2)\n", encoding="utf-8")
    (project / "a.py").write_text("print(1)\n", encoding="utf-8")
    arguments = ["scan", str(project), "--policies", str(policies), "--json"]

    first_code = main(arguments)
    first = capsys.readouterr().out
    second_code = main(arguments)
    second = capsys.readouterr().out

    assert first_code == second_code == 1
    assert first == second
    assert [item["file"] for item in json.loads(first)["findings"]] == [
        "a.py",
        "b.py",
    ]


def test_json_mode_also_structures_argument_errors(capsys):
    exit_code = main(["scan", "source.py", "--json"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert report["status"] == "ERROR"
    assert report["errors"][0]["category"] == "configuration"
    assert captured.err == ""


def test_top_level_help_lists_explicit_scan_command():
    result = runner.invoke(cli, ["--help"], prog_name="codepatrol")

    assert result.exit_code == 0
    assert "Commands:" in result.stdout
    assert "scan" in result.stdout
    assert "codepatrol scan --help" in result.stdout


def test_scan_help_lists_all_arguments_and_options():
    result = runner.invoke(cli, ["scan", "--help"], prog_name="codepatrol")

    assert result.exit_code == 0
    assert "PATH..." in result.stdout
    assert "--policies" in result.stdout
    assert "--exclude" in result.stdout
    assert "--json" in result.stdout
    assert "--verbose" in result.stdout


def test_version_and_no_argument_behavior_are_preserved():
    version = runner.invoke(cli, ["--version"], prog_name="codepatrol")
    no_arguments = runner.invoke(cli, [], prog_name="codepatrol")

    assert version.exit_code == 0
    assert version.stdout.strip() == "codepatrol 0.1.0"
    assert no_arguments.exit_code == 2
    assert "Missing command" in no_arguments.stderr


def test_typer_routes_multiple_paths_and_custom_exclusions(tmp_path):
    policies = write_print_policy(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    keep = project / "keep.py"
    keep.write_text("print(1)\n", encoding="utf-8")
    generated = project / "generated"
    generated.mkdir()
    (generated / "ignored.py").write_text("print(2)\n", encoding="utf-8")

    result = runner.invoke(
        cli,
        [
            "scan",
            str(project),
            str(keep),
            "--policies",
            str(policies),
            "--exclude",
            "generated",
            "--json",
        ],
        prog_name="codepatrol",
    )

    report = json.loads(result.stdout)
    assert result.exit_code == 1
    assert report["summary"]["files"] == {
        "excluded": 1,
        "scanned": 1,
        "selected": 1,
    }
    assert [finding["file"] for finding in report["findings"]] == ["keep.py"]
    assert result.stderr == ""


def test_json_argument_errors_are_single_documents():
    invocations = [
        ["scan", "--json"],
        ["scan", "source.py", "--unknown-option", "--json"],
        [
            "scan",
            "source.py",
            "--policies",
            "policies.yaml",
            "--exclude",
            "--json",
        ],
    ]

    for arguments in invocations:
        result = runner.invoke(cli, arguments, prog_name="codepatrol")
        report = json.loads(result.stdout)
        assert result.exit_code == 2
        assert report["status"] == "ERROR"
        assert report["errors"][0]["category"] == "configuration"
        assert result.stderr == ""


def test_help_and_version_remain_text_when_combined_with_json():
    invocations = [
        (["--help", "--json"], "Commands:"),
        (["--json", "--version"], "codepatrol 0.1.0"),
        (["scan", "--json", "--help"], "--policies"),
    ]

    for arguments, expected in invocations:
        result = runner.invoke(cli, arguments, prog_name="codepatrol")
        assert result.exit_code == 0
        assert expected in result.stdout
        assert not result.stdout.lstrip().startswith("{")


def test_installed_console_entry_point_smoke_test():
    entry_point = Path(sys.executable).with_name("codepatrol")
    assert entry_point.is_file()

    for arguments, expected in [
        (["--version"], "codepatrol 0.1.0"),
        (["scan", "--help"], "--policies"),
    ]:
        completed = subprocess.run(
            [str(entry_point), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0
        assert expected in completed.stdout
        assert completed.stderr == ""
