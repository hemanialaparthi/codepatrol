"""Load and validate YAML policy files."""

from pathlib import Path
from typing import Any

import yaml

from codepatrol.errors import PolicyError
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import Policy

REQUIRED_FIELDS = ("id", "language", "pattern", "message")
TOP_LEVEL_FIELDS = {"rules"}


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silently overwritten mapping keys."""


def _construct_unique_mapping(
    loader: yaml.Loader, node: yaml.Node, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_policies(path: Path, registry: LanguageRegistry) -> list[Policy]:
    """Load policies from *path*, rejecting incomplete or ambiguous input."""
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PolicyError(f"Could not read policy file '{path}': {exc}") from exc

    try:
        document = yaml.load(contents, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise PolicyError(f"Malformed YAML in '{path}': {exc}") from exc

    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        raise PolicyError("Policy file must contain a top-level 'rules' list.")
    unknown_top_level = set(document) - TOP_LEVEL_FIELDS
    if unknown_top_level:
        fields = ", ".join(sorted(map(str, unknown_top_level)))
        raise PolicyError(f"Unknown top-level policy field(s): {fields}.")
    if not document["rules"]:
        raise PolicyError("Policy file must contain at least one rule.")

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

    unknown_fields = set(raw_rule) - set(REQUIRED_FIELDS)
    if unknown_fields:
        fields = ", ".join(sorted(map(str, unknown_fields)))
        raise PolicyError(f"Rule {index} has unknown field(s): {fields}.")

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
