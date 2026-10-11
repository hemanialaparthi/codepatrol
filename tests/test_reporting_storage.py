import os

import pytest

from codepatrol.reporting.storage import (
    save_report_atomic,
    validate_report_destination,
)


def test_atomic_report_save_creates_parent_and_replaces_from_sibling(
    tmp_path, monkeypatch
):
    destination = tmp_path / "nested" / "report.json"
    replacements = []
    original_replace = os.replace

    def record_replace(source, target):
        replacements.append((source, target))
        original_replace(source, target)

    monkeypatch.setattr("codepatrol.reporting.storage.os.replace", record_replace)

    save_report_atomic('{"status":"PASS"}', destination)

    assert destination.read_text(encoding="utf-8") == '{"status":"PASS"}\n'
    assert len(replacements) == 1
    temporary, target = replacements[0]
    assert os.path.dirname(temporary) == str(destination.parent)
    assert target == destination
    assert not os.path.exists(temporary)


def test_policy_test_and_project_config_destinations_are_protected(tmp_path):
    policy = tmp_path / "policies.yaml"
    policy.write_text("rules: []\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_app.py"
    test_file.write_text("pass\n")
    config = tmp_path / "pyproject.toml"
    config.write_text("[project]\n")

    for destination in (policy, test_file, config):
        with pytest.raises(ValueError):
            validate_report_destination(
                destination,
                source_inputs=[tmp_path / "src"],
                policy_file=policy,
                command_arguments=["tests"],
                command_working_directory=tmp_path,
            )
