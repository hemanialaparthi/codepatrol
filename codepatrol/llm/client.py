"""Lazy, single-request LiteLLM integration with redacted failures."""

import importlib.metadata
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from codepatrol.llm.models import LLMRepairConfig, ProviderResponse, RepairRequest
from codepatrol.llm.prompt import render_messages


class LLMConfigurationError(Exception):
    """The LLM extra or explicit credential configuration is unavailable."""


class LLMProviderError(Exception):
    """The provider could not produce a usable response."""


@dataclass(frozen=True)
class LoadedLiteLLM:
    completion: object
    version: str


def installed_version() -> str | None:
    try:
        return importlib.metadata.version("litellm-core")
    except importlib.metadata.PackageNotFoundError:
        return None


def request_repair(config: LLMRepairConfig, request: RepairRequest) -> ProviderResponse:
    loaded = _load_litellm()
    api_key = _resolve_key(config.api_key_env)
    kwargs: dict[str, object] = {
        "model": config.model,
        "messages": render_messages(request),
        "timeout": config.timeout,
        "max_tokens": config.max_tokens,
        "stream": False,
        "num_retries": 0,
        "max_retries": 0,
    }
    if config.api_base is not None:
        kwargs["api_base"] = config.api_base
    if api_key is not None:
        kwargs["api_key"] = api_key
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature
    if config.json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    started = time.monotonic()
    try:
        response = loaded.completion(**kwargs)  # type: ignore[operator]
    except Exception as exc:
        raise LLMProviderError(
            f"LiteLLM provider request failed ({type(exc).__name__}); see provider logs for details."
        ) from exc
    elapsed = time.monotonic() - started
    content, content_kind, finish_reason, usage = _response_values(response)
    return ProviderResponse(
        content=content,
        content_kind=content_kind,
        finish_reason=finish_reason,
        usage=usage,
        elapsed_seconds=elapsed,
        version=loaded.version,
    )


def _load_litellm() -> LoadedLiteLLM:
    try:
        importlib.metadata.version("litellm")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise LLMConfigurationError(
            "Both litellm and litellm-core are installed; remove one because their files overlap."
        )
    try:
        from litellm import completion
    except ImportError as exc:
        raise LLMConfigurationError(
            "LLM repair requires the optional dependency: "
            "uv sync --extra llm"
        ) from exc
    version = installed_version()
    if version is None:
        raise LLMConfigurationError(
            "The LiteLLM import is available but the litellm-core distribution metadata is missing."
        )
    return LoadedLiteLLM(completion=completion, version=version)


def _resolve_key(name: str | None) -> str | None:
    if name is None:
        return None
    value = os.environ.get(name)
    if not value:
        raise LLMConfigurationError(
            f"The environment variable named by --llm-api-key-env is missing or empty: {name}."
        )
    return value


def endpoint_identifier(api_base: str | None) -> str:
    if api_base is None:
        return "provider-default"
    parsed = urlsplit(api_base)
    if parsed.scheme and parsed.hostname:
        hostname = parsed.hostname
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{hostname}{port}"
    return "custom-endpoint"


def _response_values(response: object) -> tuple[str | None, str, str | None, dict[str, int]]:
    choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise LLMProviderError("LiteLLM returned an unsupported response choice count.")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    finish = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
    raw_usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw_usage.get(key) if isinstance(raw_usage, dict) else getattr(raw_usage, key, None)
        if isinstance(value, int) and value >= 0:
            usage[key] = value
    if isinstance(content, str):
        content_value, content_kind = content, "string"
    elif content is None:
        content_value, content_kind = None, "absent"
    else:
        content_value, content_kind = None, f"non-string:{type(content).__name__}"
    return content_value, content_kind, finish if isinstance(finish, str) else None, usage
