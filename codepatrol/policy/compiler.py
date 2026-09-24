"""Compile validated policies through their language adapters."""

from collections.abc import Iterable

from codepatrol.errors import PolicyError
from codepatrol.languages.registry import LanguageRegistry
from codepatrol.policy.models import CompiledPolicy, Policy


def compile_policies(
    policies: Iterable[Policy], registry: LanguageRegistry
) -> list[CompiledPolicy]:
    compiled: list[CompiledPolicy] = []
    for policy in policies:
        adapter = registry.get(policy.language)
        try:
            pattern = adapter.compile_pattern(policy.pattern)
        except PolicyError:
            raise
        except Exception as exc:
            raise PolicyError(
                f"Rule '{policy.id}' has an invalid pattern: {exc}"
            ) from exc
        compiled.append(CompiledPolicy(policy=policy, pattern=pattern))
    return compiled

