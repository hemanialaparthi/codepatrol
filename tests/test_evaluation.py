import json
import shlex
import sys
from pathlib import Path

import pytest

from codepatrol.cli import main
from codepatrol.evaluation import EvaluationResult, evaluate_scan
from codepatrol.scanner import CheckError, ScanResult, Violation


def command_for(script: Path) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def write_policy(tmp_path):
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """rules:
  - id: no-print
    language: python
    pattern: print($X)
    message: Use logging.
""",
        encoding="utf-8",
    )
    return policy


def test_combined_policy_and_test_pass_fail_cases(tmp_path):
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote('raise SystemExit(0)')}"
    test_fail = f"{shlex.quote(sys.executable)} -c {shlex.quote('raise SystemExit(4)')}"

    both_pass = evaluate_scan(
        ScanResult(root=tmp_path), command, test_working_directory=tmp_path
    )
    policy_fail = evaluate_scan(
        ScanResult(
            violations=[
                Violation(
                    rule_id="rule",
                    message="finding",
                    file=tmp_path / "source.py",
                    line=1,
                    column=1,
                    end_line=1,
                    end_column=2,
                    matched_source="x",
                )
            ],
            root=tmp_path,
        ),
        command,
        test_working_directory=tmp_path,
    )
    functional_fail = evaluate_scan(
        ScanResult(root=tmp_path), test_fail, test_working_directory=tmp_path
    )

    assert (both_pass.status, both_pass.exit_code) == ("PASS", 0)
    assert (policy_fail.status, policy_fail.exit_code) == ("FAIL", 1)
    assert (functional_fail.status, functional_fail.exit_code) == ("FAIL", 1)


def test_blocking_policy_error_skips_execution(tmp_path):
    marker = tmp_path / "ran"
    script = tmp_path / "run.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    scan = ScanResult(
        errors=[CheckError(category="discovery", message="missing")], root=tmp_path
    )

    result = evaluate_scan(scan, command_for(script), test_working_directory=tmp_path)

    assert result.status == "ERROR"
    assert result.test_command is None
    assert "discovery" in result.test_skip_reason
    assert not marker.exists()


def test_syntax_only_errors_still_run_tests(tmp_path):
    script = tmp_path / "run.py"
    script.write_text("print('diagnostic')\n", encoding="utf-8")
    scan = ScanResult(
        errors=[CheckError(category="parsing", message="syntax")], root=tmp_path
    )

    result = evaluate_scan(scan, command_for(script), test_working_directory=tmp_path)

    assert result.status == "ERROR"
    assert result.test_command is not None
    assert result.test_command.outcome == "PASS"


def test_cli_json_isolated_and_output_matches(tmp_path, capsys):
    policy = write_policy(tmp_path)
    source = tmp_path / "clean.py"
    source.write_text("value = 1\n", encoding="utf-8")
    script = tmp_path / "noisy.py"
    script.write_text("print('CHILD OUTPUT')\n", encoding="utf-8")
    report_path = tmp_path / "reports" / "evaluation.json"

    exit_code = main(
        [
            "scan",
            str(source),
            "--policies",
            str(policy),
            "--test-command",
            command_for(script),
            "--json",
            "--output",
            str(report_path),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["schema_version"] == "evaluation-1.0"
    assert report["policy_scan"]["schema_version"] == "1.0"
    assert report["test_command"]["stdout"]["preview"] == "CHILD OUTPUT\n"
    assert captured.out.count("CHILD OUTPUT") == 1
    assert report_path.read_text(encoding="utf-8") == captured.out
    assert captured.err == f"Saved report: {report_path.resolve()}\n"


def test_scan_only_output_retains_original_schema(tmp_path, capsys):
    policy = write_policy(tmp_path)
    source = tmp_path / "clean.py"
    source.write_text("value = 1\n", encoding="utf-8")
    report_path = tmp_path / "scan.json"

    exit_code = main(
        [
            "scan",
            str(source),
            "--policies",
            str(policy),
            "--json",
            "--output",
            str(report_path),
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["schema_version"] == "1.0"
    assert "policy_scan" not in report


def test_test_only_options_require_command(tmp_path, capsys):
    policy = write_policy(tmp_path)
    source = tmp_path / "clean.py"
    source.write_text("value = 1\n", encoding="utf-8")

    exit_code = main(
        [
            "scan",
            str(source),
            "--policies",
            str(policy),
            "--test-timeout",
            "5",
            "--json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["schema_version"] == "1.0"
    assert "requires --test-command" in report["errors"][0]["message"]


def test_protected_output_destination_skips_command(tmp_path, capsys):
    policy = write_policy(tmp_path)
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    marker = tmp_path / "ran"
    script = tmp_path / "run.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    exit_code = main(
        [
            "scan",
            str(source),
            "--policies",
            str(policy),
            "--test-command",
            command_for(script),
            "--output",
            str(source),
            "--json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["status"] == "ERROR"
    assert report["test_command"] is None
    assert source.read_text() == "value = 1\n"
    assert not marker.exists()


def test_report_write_failure_is_structured_without_traceback(
    tmp_path, capsys, monkeypatch
):
    policy = write_policy(tmp_path)
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text("pass\n", encoding="utf-8")

    def fail_save(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr("codepatrol.cli.save_report_atomic", fail_save)
    exit_code = main(
        [
            "scan",
            str(source),
            "--policies",
            str(policy),
            "--test-command",
            command_for(script),
            "--output",
            str(tmp_path / "report.json"),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert report["status"] == "ERROR"
    assert report["errors"][0]["category"] == "reporting"
    assert "Traceback" not in captured.out + captured.err
