import json
from pathlib import Path

from codepatrol.cli import main
from codepatrol.llm.client import LoadedLiteLLM, endpoint_identifier, request_repair
from codepatrol.llm.controller import _candidate_diff
from codepatrol.llm.debug import validate_debug_destination, write_debug_response
from codepatrol.llm.models import LLMRepairConfig, ProviderResponse, RepairRequest
from codepatrol.llm.prompt import render_messages
from codepatrol.llm.response import LLMResponseError, parse_response


def write_policy(tmp_path: Path) -> Path:
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """rules:
  - id: no-legacy
    language: python
    pattern: legacy_send($X)
    message: Use modern_send.
""",
        encoding="utf-8",
    )
    return policy


def test_llm_candidate_uses_staging_and_applies_after_validation(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("legacy_send(value)\n", encoding="utf-8")
    policy = write_policy(tmp_path)

    def fake_request(config, request):
        assert config.model == "openai/test-model"
        assert request.source == "legacy_send(value)\n"
        assert request.policies[0]["expression"] == "legacy_send($X)"
        return ProviderResponse(
            content=json.dumps({
                "outcome": "candidate",
                "replacement": "modern_send(value)\n",
                "explanation": "Rename the legacy call.",
            }),
            finish_reason="stop",
            usage={"total_tokens": 12},
        )

    monkeypatch.setattr("codepatrol.llm.controller.request_repair", fake_request)
    code = main([
        "scan", str(project), "--policies", str(policy), "--fix-with-llm",
        "--llm-model", "openai/test-model",
    ])

    output = capsys.readouterr().out
    assert code == 0
    assert "Candidate diff:" in output
    assert "Application: applied after staged validation" in output
    assert source.read_text(encoding="utf-8") == "modern_send(value)\n"


def test_retained_candidate_shape_parses_and_passes_staged_validation(
    tmp_path, monkeypatch, capsys
):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "example.py"
    original = """def calculate_total(values):
    return sum(values)


def legacy_total(values):
    raise RuntimeError(\"Legacy function must not be used\")


def checkout_total(values):
    return legacy_total(values)"""
    replacement = """def calculate_total(values):
    return sum(values)


def legacy_total(values):
    raise RuntimeError(\"Legacy function must not be used\")


def checkout_total(values):
    return calculate_total(values)"""
    source.write_text(original, encoding="utf-8")
    policy = tmp_path / "policies.yaml"
    policy.write_text(
        """rules:
  - id: replace-legacy-total
    language: python
    pattern: legacy_total($VALUES)
    message: Replace the legacy call.
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "codepatrol.llm.controller.request_repair",
        lambda config, request: ProviderResponse(
            content=json.dumps({"outcome": "candidate", "replacement": replacement}),
            finish_reason="stop",
        ),
    )

    code = main([
        "scan", str(source), "--policies", str(policy), "--fix-with-llm",
        "--llm-model", "openai/test-model", "--project-root", str(project), "--json",
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["response"]["candidate_parsed"] is True
    assert report["staged"]["status"] == "PASS"
    assert report["application"] == "applied after staged validation"
    assert source.read_text(encoding="utf-8") == replacement


def test_clean_initial_evaluation_does_not_request_llm(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    policy = write_policy(tmp_path)
    called = False

    def fail_request(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("model request should not occur")

    monkeypatch.setattr("codepatrol.llm.controller.request_repair", fail_request)
    code = main([
        "scan", str(project), "--policies", str(policy), "--fix-with-llm",
        "--llm-model", "openai/test-model", "--json",
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert called is False
    assert report["request"]["attempted"] is False
    assert report["initial"]["status"] == "PASS"


def test_deterministic_fix_does_not_request_llm(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("legacy_send(value)\n", encoding="utf-8")
    policy = write_policy(tmp_path)

    monkeypatch.setattr(
        "codepatrol.llm.controller.request_repair",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected LLM request")),
    )
    assert main(["scan", str(project), "--policies", str(policy), "--fix"]) == 1
    assert source.read_text(encoding="utf-8") == "legacy_send(value)\n"


def test_ambiguous_source_selection_is_rejected_before_request(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("legacy_send(a)\n", encoding="utf-8")
    (project / "b.py").write_text("legacy_send(b)\n", encoding="utf-8")
    policy = write_policy(tmp_path)
    monkeypatch.setattr(
        "codepatrol.llm.controller.request_repair",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected LLM request")),
    )

    code = main([
        "scan", str(project), "--policies", str(policy), "--fix-with-llm",
        "--llm-model", "openai/test-model", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["request"]["attempted"] is False
    assert "exactly one discovered" in report["errors"][0]["message"]


def test_rejected_candidate_preserves_original_bytes(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    original = "legacy_send(value)\n"
    source.write_text(original, encoding="utf-8")
    policy = write_policy(tmp_path)
    monkeypatch.setattr(
        "codepatrol.llm.controller.request_repair",
        lambda config, request: ProviderResponse(
            content=json.dumps({
                "outcome": "candidate",
                "replacement": "def broken(:\n    pass\n",
                "explanation": "bad candidate",
            }),
            finish_reason="stop",
        ),
    )

    code = main([
        "scan", str(project), "--policies", str(policy), "--fix-with-llm",
        "--llm-model", "openai/test-model", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["staged"]["status"] == "ERROR"
    assert report["source_preserved"] is True
    assert source.read_text(encoding="utf-8") == original


def test_litellm_client_forwards_settings_without_logging_key(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [{
                "message": {"content": '{"outcome":"needs_guidance","explanation":"missing intent"}'},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 4},
        }

    monkeypatch.setenv("CODEPATROL_TEST_KEY", "secret-value")
    monkeypatch.setattr(
        "codepatrol.llm.client._load_litellm",
        lambda: LoadedLiteLLM(fake_completion, "1.108.2"),
    )
    response = request_repair(
        LLMRepairConfig("openai/test", "http://localhost:8000/v1?x=1", "CODEPATROL_TEST_KEY", 4.0, 128, 0.0),
        RepairRequest(Path("app.py"), "pass\n", tuple(), "tests not run", "none"),
    )
    assert captured["model"] == "openai/test"
    assert captured["api_base"] == "http://localhost:8000/v1?x=1"
    assert captured["api_key"] == "secret-value"
    assert captured["timeout"] == 4.0
    assert captured["max_tokens"] == 128
    assert captured["temperature"] == 0.0
    assert captured["num_retries"] == 0
    assert captured["max_retries"] == 0
    assert response.usage == {"prompt_tokens": 4}


def test_json_mode_forwards_response_format_and_endpoint_is_sanitized(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [{
                "message": {"content": '{"outcome":"needs_guidance","explanation":"missing intent"}'},
                "finish_reason": "stop",
            }]
        }

    monkeypatch.setattr(
        "codepatrol.llm.client._load_litellm",
        lambda: LoadedLiteLLM(fake_completion, "1.108.2"),
    )
    request_repair(
        LLMRepairConfig("openai/test", "http://user:secret@localhost:11434/v1?token=secret#fragment", None, 4.0, 128, None, True),
        RepairRequest(Path("app.py"), "pass\n", tuple(), "tests not run", "none"),
    )
    assert captured["response_format"] == {"type": "json_object"}
    assert endpoint_identifier("http://user:secret@localhost:11434/v1?token=secret#fragment") == "http://localhost:11434"
    assert endpoint_identifier("https://example.test") == "https://example.test"


def test_debug_artifact_preserves_malformed_content_and_permissions(tmp_path):
    destination = tmp_path / "debug" / "response.json"
    raw = "```json\nnot-json\n```\n"
    write_debug_response(
        destination,
        model="openai/test",
        endpoint_id="http://localhost:11434",
        finish_reason="stop",
        usage={"completion_tokens": 3},
        content=raw,
        content_kind="string",
    )
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["content"] == raw
    assert destination.stat().st_mode & 0o777 == 0o600


def test_debug_destination_rejects_policy_source_tests_and_report(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("pass\n", encoding="utf-8")
    policy = tmp_path / "policies.yaml"
    policy.write_text("rules: []\n", encoding="utf-8")
    report = tmp_path / "report.json"
    for destination in [source, policy, project / "tests" / "response.json", report]:
        try:
            validate_debug_destination(
                destination,
                source_inputs=[source, project],
                policy_file=policy,
                normal_report=report,
                test_arguments=["python", "-m", "pytest", "tests"],
                test_cwd=project,
            )
        except Exception:
            pass
        else:
            raise AssertionError(f"protected debug destination was accepted: {destination}")


def test_repair_prompt_requires_top_level_complete_file_contract():
    request = RepairRequest(
        Path("app.py"),
        "def helper(value):\n    return value\n\n\ndef caller(value):\n    return legacy(value)\n",
        tuple(),
        "tests not run",
        "no bounded project instructions found",
    )
    messages = render_messages(request)
    system = messages[0]["content"]
    contract = json.loads(messages[1]["content"])["response_contract"]

    assert "complete contents" in system
    assert "never a snippet, diff, patch" in system
    assert "exactly one top-level JSON object" in contract
    assert "Do not wrap it inside 'candidate', 'result', 'response'" in contract
    assert "ENTIRE updated Python file" in contract
    assert "'explanation' is optional" in contract
    assert "unrelated formatting examples" in contract
    assert '"outcome":"candidate"' in contract
    assert '"outcome":"needs_guidance"' in contract
    assert "def helper(value):" in contract
    assert "def caller(value):" in contract


def test_nested_candidate_response_is_rejected_with_missing_top_level_outcome():
    response = ProviderResponse(
        content=json.dumps({
            "candidate": {
                "outcome": "candidate",
                "replacement": "def caller(value):\n    return modern(value)\n",
                "explanation": "partial function",
            }
        }),
        finish_reason="stop",
    )
    try:
        parse_response(response)
    except LLMResponseError as exc:
        assert "missing the required top-level 'outcome'" in str(exc)
        assert "nested candidate/result objects are not accepted" in str(exc)
    else:
        raise AssertionError("nested candidate response was accepted")


def test_candidate_missing_and_unexpected_fields_are_distinguished():
    missing = ProviderResponse(
        content=json.dumps({"outcome": "candidate"}),
        finish_reason="stop",
    )
    unexpected = ProviderResponse(
        content=json.dumps({
            "outcome": "candidate",
            "replacement": "pass\n",
            "extra": "not allowed",
        }),
        finish_reason="stop",
    )
    for response, expected in [
        (missing, "missing required field(s): 'replacement'"),
        (unexpected, "contains unexpected field(s): 'extra'"),
    ]:
        try:
            parse_response(response)
        except LLMResponseError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("invalid candidate response was accepted")


def test_candidate_explanation_is_optional_but_typed_when_present():
    parsed = parse_response(
        ProviderResponse(
            content=json.dumps({"outcome": "candidate", "replacement": "pass\n"}),
            finish_reason="stop",
        )
    )
    assert parsed.explanation == ""

    try:
        parse_response(
            ProviderResponse(
                content=json.dumps({
                    "outcome": "candidate",
                    "replacement": "pass\n",
                    "explanation": None,
                }),
                finish_reason="stop",
            )
        )
    except LLMResponseError as exc:
        assert str(exc) == "Candidate explanation must be a string."
    else:
        raise AssertionError("non-string candidate explanation was accepted")


def test_candidate_diff_separates_lines_when_both_versions_end_with_newline():
    diff = _candidate_diff(Path("example.py"), "return legacy(values)\n", "return modern(values)\n")
    assert "-return legacy(values)\n+return modern(values)\n" in diff
    assert "\\ No newline at end of file" not in diff


def test_candidate_diff_marks_only_original_missing_final_newline():
    diff = _candidate_diff(Path("example.py"), "return legacy(values)", "return modern(values)\n")
    assert "-return legacy(values)\n\\ No newline at end of file\n+return modern(values)\n" in diff


def test_candidate_diff_marks_only_candidate_missing_final_newline():
    diff = _candidate_diff(Path("example.py"), "return legacy(values)\n", "return modern(values)")
    assert "+return modern(values)\n\\ No newline at end of file\n" in diff


def test_candidate_diff_marks_both_missing_final_newlines():
    diff = _candidate_diff(Path("example.py"), "return legacy(values)", "return modern(values)")
    assert "-return legacy(values)\n\\ No newline at end of file\n+return modern(values)\n\\ No newline at end of file\n" in diff


def test_candidate_diff_handles_empty_file_changes():
    added = _candidate_diff(Path("example.py"), "", "value = 1")
    removed = _candidate_diff(Path("example.py"), "value = 1", "")
    assert "+value = 1\n\\ No newline at end of file\n" in added
    assert "-value = 1\n\\ No newline at end of file\n" in removed


def test_candidate_diff_normalizes_crlf_only_for_display():
    diff = _candidate_diff(
        Path("example.py"),
        "return legacy(values)\r\n",
        "return modern(values)\r\n",
    )
    assert "-return legacy(values)\n+return modern(values)\n" in diff
    assert "\r" not in diff


def test_candidate_diff_does_not_mutate_source_strings():
    original = "return legacy(values)\r\n"
    candidate = "return modern(values)"
    original_before = original.encode("utf-8")
    candidate_before = candidate.encode("utf-8")
    _candidate_diff(Path("example.py"), original, candidate)
    assert original.encode("utf-8") == original_before
    assert candidate.encode("utf-8") == candidate_before


def test_unsupported_outcome_diagnostic_is_bounded_and_escaped():
    outcome = "bad\nvalue" + "x" * 200
    response = ProviderResponse(
        content=json.dumps({
            "outcome": outcome,
            "replacement": "pass\n",
            "explanation": "unused",
        }),
        finish_reason="stop",
    )
    try:
        parse_response(response)
    except LLMResponseError as exc:
        message = str(exc)
        assert "unsupported top-level outcome" in message
        assert "bad\\nvalue" in message
        assert len(message) < 220
    else:
        raise AssertionError("unsupported outcome was accepted")
