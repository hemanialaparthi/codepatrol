"""Compile validated policies through their language adapters."""

from collections.abc import Iterable

from lxml import etree

from codepatrol.errors import PolicyError
from codepatrol.languages.base import CompiledPattern
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import CompiledFix, CompiledPolicy, CompiledXPath, Policy
from codepatrol.fixing.replacement import compile_replacement


def compile_policies(
    policies: Iterable[Policy], registry: LanguageRegistry
) -> list[CompiledPolicy]:
    compiled: list[CompiledPolicy] = []
    for policy in policies:
        adapter = registry.get(policy.language)
        if policy.pattern is not None:
            try:
                pattern = adapter.compile_pattern(policy.pattern)
            except PolicyError as exc:
                raise PolicyError(
                    f"Rule '{policy.id}' has an invalid pattern: {exc}",
                    policy_id=policy.id,
                ) from exc
            except Exception as exc:
                raise PolicyError(
                    f"Rule '{policy.id}' has an invalid pattern: {exc}",
                    policy_id=policy.id,
                ) from exc
            fix = _compile_fix(policy, pattern)
            compiled.append(CompiledPolicy(policy=policy, pattern=pattern, fix=fix))
            continue

        assert policy.xpath is not None
        try:
            xpath = etree.XPath(policy.xpath)
        except (etree.XPathError, TypeError) as exc:
            raise PolicyError(
                f"Rule '{policy.id}' has an invalid XPath 1.0 expression: {exc}",
                policy_id=policy.id,
            ) from exc
        fix = _compile_fix(policy, None)
        compiled.append(
            CompiledPolicy(
                policy=policy, xpath=CompiledXPath(expression=xpath), fix=fix
            )
        )
    return compiled


def _compile_fix(
    policy: Policy, pattern: CompiledPattern | None
) -> CompiledFix | None:
    if policy.fix is None:
        return None
    try:
        return compile_replacement(policy.fix.replacement, pattern)
    except PolicyError as exc:
        raise PolicyError(
            f"Rule '{policy.id}' has an invalid fix: {exc}", policy_id=policy.id
        ) from exc
