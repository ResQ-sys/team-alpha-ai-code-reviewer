"""Tests for ``LLMClient.complete_schema`` structured-output enforcement.

All tests mock the underlying ``complete`` (or the ollama transport) so no
real LLM is called and no network request is made.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from utils.llm_client import LLMClient


SCHEMA = {
    "type": "object",
    "required": ["severity", "explanation"],
    "properties": {
        "severity": {"type": "string"},
        "explanation": {"type": "string"},
    },
}


def _client(provider: str = "anthropic") -> LLMClient:
    return LLMClient(provider=provider)


def test_returns_parsed_dict_on_valid_json_first_try():
    good = '{"severity": "HIGH", "explanation": "SQL injection"}'
    with patch.object(LLMClient, "complete", return_value=good) as mock_complete:
        result = _client().complete_schema("sys", "prompt", SCHEMA)

    assert result == {"severity": "HIGH", "explanation": "SQL injection"}
    assert mock_complete.call_count == 1


def test_retries_on_malformed_then_succeeds():
    responses = [
        "not json at all {broken",
        '{"severity": "LOW", "explanation": "weak hash"}',
    ]
    with patch.object(LLMClient, "complete", side_effect=responses) as mock_complete:
        result = _client().complete_schema("sys", "prompt", SCHEMA, retries=2)

    assert result == {"severity": "LOW", "explanation": "weak hash"}
    assert mock_complete.call_count == 2


def test_retries_on_missing_required_key_then_succeeds():
    responses = [
        '{"severity": "MEDIUM"}',  # missing "explanation"
        '{"severity": "MEDIUM", "explanation": "hardcoded secret"}',
    ]
    with patch.object(LLMClient, "complete", side_effect=responses) as mock_complete:
        result = _client().complete_schema("sys", "prompt", SCHEMA, retries=2)

    assert result["explanation"] == "hardcoded secret"
    assert mock_complete.call_count == 2


def test_permanently_bad_json_returns_parse_error():
    bad = "this is definitely not json"
    with patch.object(LLMClient, "complete", return_value=bad) as mock_complete:
        result = _client().complete_schema("sys", "prompt", SCHEMA, retries=2)

    assert result["_parse_error"] is True
    assert result["_raw"] == bad
    # first attempt + 2 retries == 3 total.
    assert mock_complete.call_count == 3


def test_persistently_missing_key_returns_parse_error():
    bad = '{"severity": "HIGH"}'  # never includes "explanation"
    with patch.object(LLMClient, "complete", return_value=bad) as mock_complete:
        result = _client().complete_schema("sys", "prompt", SCHEMA, retries=1)

    assert result["_parse_error"] is True
    assert mock_complete.call_count == 2


def test_retries_zero_means_single_attempt():
    bad = "nope"
    with patch.object(LLMClient, "complete", return_value=bad) as mock_complete:
        result = _client().complete_schema("sys", "prompt", SCHEMA, retries=0)

    assert result["_parse_error"] is True
    assert mock_complete.call_count == 1


def test_empty_required_accepts_any_valid_json():
    schema = {"type": "object", "required": []}
    with patch.object(LLMClient, "complete", return_value='{"anything": 1}'):
        result = _client().complete_schema("sys", "prompt", schema)

    assert result == {"anything": 1}


def test_non_ollama_steers_system_prompt_with_schema():
    good = '{"severity": "HIGH", "explanation": "x"}'
    captured = {}

    def _fake_complete(self, system, prompt, max_tokens=1500):
        captured["system"] = system
        return good

    with patch.object(LLMClient, "complete", _fake_complete):
        _client("anthropic").complete_schema("base-sys", "prompt", SCHEMA)

    assert "severity" in captured["system"]
    assert "explanation" in captured["system"]
    assert "JSON" in captured["system"]


def test_ollama_uses_native_json_format_flag():
    good = '{"severity": "HIGH", "explanation": "x"}'

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": good}

    with patch("utils.llm_client.requests.post", return_value=_Resp()) as mock_post:
        result = _client("ollama").complete_schema("sys", "prompt", SCHEMA)

    assert result == {"severity": "HIGH", "explanation": "x"}
    # Verify the ollama request body carried the native JSON-mode flag.
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["format"] == "json"


def test_corrective_instruction_added_on_retry():
    responses = ["garbage", '{"severity": "LOW", "explanation": "ok"}']
    seen_systems = []

    def _fake_complete(self, system, prompt, max_tokens=1500):
        seen_systems.append(system)
        return responses[len(seen_systems) - 1]

    with patch.object(LLMClient, "complete", _fake_complete):
        result = _client("openai").complete_schema("sys", "prompt", SCHEMA, retries=2)

    assert result["severity"] == "LOW"
    # The second attempt's system prompt should include the correction text.
    assert "rejected" in seen_systems[1].lower()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
