"""Strict full-file response parsing."""

import json
from typing import Any

from codepatrol.llm.models import MAX_RESPONSE_BYTES, ParsedRepair, ProviderResponse


class LLMResponseError(Exception):
    """The provider response does not satisfy CodePatrol's repair contract."""


def parse_response(response: ProviderResponse) -> ParsedRepair:
    if response.finish_reason not in {"stop"}:
        raise LLMResponseError(
            f"The model response was incomplete or unsupported (finish reason: {response.finish_reason!r})."
        )
    if response.content is None or not response.content.strip():
        raise LLMResponseError("The model returned an empty repair response.")
    if len(response.content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise LLMResponseError("The model repair response exceeded the response size limit.")
    try:
        value = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"The model returned malformed repair JSON: {exc.msg}.") from exc
    if not isinstance(value, dict):
        raise LLMResponseError("The model repair response must be a JSON object.")
    outcome = value.get("outcome")
    if outcome == "candidate":
        _require_fields(
            value,
            required={"outcome", "replacement"},
            allowed={"outcome", "replacement", "explanation"},
            label="Candidate",
        )
        replacement = value.get("replacement")
        explanation = value.get("explanation")
        if not isinstance(replacement, str) or not replacement.strip():
            raise LLMResponseError("Candidate replacement must be nonempty source text.")
        if "explanation" in value and not isinstance(explanation, str):
            raise LLMResponseError("Candidate explanation must be a string.")
        replacement.encode("utf-8")
        return ParsedRepair("candidate", replacement, explanation if isinstance(explanation, str) else "")
    if outcome == "needs_guidance":
        _require_fields(
            value,
            required={"outcome", "explanation"},
            allowed={"outcome", "explanation"},
            label="Needs-guidance",
        )
        explanation = value.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            raise LLMResponseError("Needs-guidance explanation must be nonempty text.")
        return ParsedRepair("needs_guidance", explanation=explanation)
    if "outcome" not in value:
        raise LLMResponseError(
            "The model repair response is missing the required top-level 'outcome' field; "
            "nested candidate/result objects are not accepted."
        )
    if isinstance(outcome, str):
        bounded = outcome[:80].encode("unicode_escape").decode("ascii")
        suffix = "..." if len(outcome) > 80 else ""
        detail = f"{bounded}{suffix}"
    else:
        detail = f"value of type {type(outcome).__name__}"
    raise LLMResponseError(
        "The model repair response has unsupported top-level outcome "
        f"{detail}; expected 'candidate' or 'needs_guidance'."
    )


def _require_fields(
    value: dict[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    label: str,
) -> None:
    missing = sorted(required - set(value))
    if missing:
        fields = ", ".join(repr(field) for field in missing)
        raise LLMResponseError(f"{label} response is missing required field(s): {fields}.")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        fields = ", ".join(repr(field) for field in unexpected)
        raise LLMResponseError(f"{label} response contains unexpected field(s): {fields}.")
