from codepatrol.cli import main


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
    assert "FAILED" in output


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
    assert "PASSED" in output


def test_cli_returns_two_for_bad_configuration(tmp_path, capsys):
    exit_code = main(
        ["scan", str(tmp_path / "missing"), "--policies", "missing.yaml"]
    )

    assert exit_code == 2
    assert "Project does not exist" in capsys.readouterr().err
