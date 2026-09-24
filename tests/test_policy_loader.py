from pathlib import Path

import pytest

from codepatrol.errors import PolicyError
from codepatrol.languages import default_registry
from codepatrol.policy.loader import load_policies


def write_policy(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_yaml_loads(tmp_path):
    path = write_policy(
        tmp_path / "policies.yaml",
        """rules:
  - id: no-print
    language: python
    pattern: print($MSG)
    message: Use logging.
""",
    )

    policies = load_policies(path, default_registry())

    assert len(policies) == 1
    assert policies[0].id == "no-print"


@pytest.mark.parametrize("field", ["id", "language", "pattern", "message"])
def test_missing_required_field_fails(tmp_path, field):
    values = {
        "id": "no-print",
        "language": "python",
        "pattern": "print($MSG)",
        "message": "Use logging.",
    }
    del values[field]
    rule = "\n".join(f"    {key}: {value}" for key, value in values.items())
    path = write_policy(tmp_path / "policies.yaml", f"rules:\n  -\n{rule}\n")

    with pytest.raises(PolicyError, match=field):
        load_policies(path, default_registry())


def test_unsupported_language_fails(tmp_path):
    path = write_policy(
        tmp_path / "policies.yaml",
        """rules:
  - id: no-alert
    language: javascript
    pattern: alert($MSG)
    message: Do not alert.
""",
    )

    with pytest.raises(PolicyError, match="unsupported language"):
        load_policies(path, default_registry())


def test_duplicate_rule_ids_fail(tmp_path):
    path = write_policy(
        tmp_path / "policies.yaml",
        """rules:
  - id: duplicate
    language: python
    pattern: print($MSG)
    message: First.
  - id: duplicate
    language: python
    pattern: len($MSG)
    message: Second.
""",
    )

    with pytest.raises(PolicyError, match="Duplicate rule ID"):
        load_policies(path, default_registry())


def test_malformed_yaml_fails(tmp_path):
    path = write_policy(tmp_path / "policies.yaml", "rules: [unterminated")

    with pytest.raises(PolicyError, match="Malformed YAML"):
        load_policies(path, default_registry())

