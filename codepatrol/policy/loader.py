"""Load and validate YAML policy files."""

from pathlib import Path
from typing import Any

import yaml

from codepatrol.errors import PolicyError
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import Policy

REQUIRED_FIELDS = ("id", "language", "pattern", "message")


def load_policies(path: Path, registry: LanguageRegistry) -> list[Policy]:
    """Load policies from *path*, rejecting incomplete or ambiguous input."""
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"Could not read policy file '{path}': {exc}") from exc

    try:
        document = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise PolicyError(f"Malformed YAML in '{path}': {exc}") from exc

    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        raise PolicyError("Policy file must contain a top-level 'rules' list.")

    policies: list[Policy] = []
    seen_ids: set[str] = set()
    for index, raw_rule in enumerate(document["rules"], start=1):
        policy = _validate_rule(raw_rule, index, registry)
        if policy.id in seen_ids:
            raise PolicyError(f"Duplicate rule ID: '{policy.id}'.")
        seen_ids.add(policy.id)
        policies.append(policy)

    return policies


def _validate_rule(
    raw_rule: Any, index: int, registry: LanguageRegistry
) -> Policy:
    if not isinstance(raw_rule, dict):
        raise PolicyError(f"Rule {index} must be a mapping.")

    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = raw_rule.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PolicyError(f"Rule {index} is missing a non-empty '{field}'.")
        values[field] = value.strip()

    if not registry.supports(values["language"]):
        raise PolicyError(
            f"Rule '{values['id']}' uses unsupported language "
            f"'{values['language']}'."
        )

    return Policy(**values)

