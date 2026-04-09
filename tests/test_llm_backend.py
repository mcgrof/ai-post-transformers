import sys
from types import SimpleNamespace

import pytest

from llm_backend import _call_codex, _call_openai, _parse_json, get_llm_backend


class _FakeOpenAIClient:
    def __init__(self, api_key=None):
        self.api_key = api_key


class _FakeOpenAIModule:
    OpenAI = _FakeOpenAIClient


def test_get_llm_backend_openai_uses_sdk_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIModule())

    backend = get_llm_backend({"podcast": {"llm_backend": "openai"}})

    assert backend["type"] == "openai"
    assert backend["client"].api_key == "test-openai-key"


def test_get_llm_backend_openai_gives_clear_error_when_sdk_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openai":
            raise ModuleNotFoundError("No module named 'openai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError) as exc:
        get_llm_backend({"podcast": {"llm_backend": "openai"}})
    assert "requires the 'openai' Python package" in str(exc.value)


def _ok_choice(content='{"ok": true}', finish="stop"):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=None),
        finish_reason=finish,
    )


def _empty_choice(finish="length", refusal=None):
    return SimpleNamespace(
        message=SimpleNamespace(content=None, refusal=refusal),
        finish_reason=finish,
    )


def test_call_openai_uses_max_completion_tokens_for_gpt5_models():
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_ok_choice()])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    raw = _call_openai(client, 'gpt-5.4', 'Return exactly {"ok": true}', 0.4, 123)

    assert raw == '{"ok": true}'
    assert calls[0]['max_completion_tokens'] == 123
    assert 'max_tokens' not in calls[0]
    assert 'temperature' not in calls[0]



def test_call_openai_uses_max_tokens_for_legacy_models():
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_ok_choice()])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    raw = _call_openai(client, 'gpt-4o-mini', 'Return exactly {"ok": true}', 0.4, 77)

    assert raw == '{"ok": true}'
    assert calls[0]['max_tokens'] == 77
    assert 'max_completion_tokens' not in calls[0]
    assert calls[0]['temperature'] == 0.4


def test_call_openai_escalates_budget_on_length_truncation():
    """Reasoning model hits length on first try, succeeds on retry with bigger budget."""
    calls = []
    responses = [
        SimpleNamespace(choices=[_empty_choice(finish="length")]),
        SimpleNamespace(choices=[_empty_choice(finish="length")]),
        SimpleNamespace(choices=[_ok_choice(content='{"result": "success"}')]),
    ]

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return responses[len(calls) - 1]

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    raw = _call_openai(client, "gpt-5.4", "complex prompt", 0.4, 16000)

    assert raw == '{"result": "success"}'
    assert len(calls) == 3
    # Each retry doubles the budget
    assert calls[0]["max_completion_tokens"] == 16000
    assert calls[1]["max_completion_tokens"] == 32000
    assert calls[2]["max_completion_tokens"] == 64000


def test_call_openai_escalation_caps_at_128k():
    """Auto-escalation must not grow unbounded."""
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_empty_choice(finish="length")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    with pytest.raises(RuntimeError) as exc:
        _call_openai(client, "gpt-5.4", "prompt", 0.4, 64000)

    msg = str(exc.value)
    assert "finish_reason=length" in msg
    assert "attempts=" in msg
    # Should escalate 64K -> 128K (cap), then give up
    budgets = [c["max_completion_tokens"] for c in calls]
    assert budgets[0] == 64000
    assert budgets[-1] == 128000
    # No budget should exceed the cap
    assert all(b <= 128000 for b in budgets)


def test_call_openai_does_not_escalate_legacy_models():
    """Legacy chat models with finish=length should NOT auto-escalate.

    They have a separate input/output budget so length-truncation
    on the output side is not recoverable by giving more budget —
    it just means the response was cut off mid-stream.
    """
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_empty_choice(finish="length")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    with pytest.raises(RuntimeError):
        _call_openai(client, "gpt-4o-mini", "prompt", 0.4, 16000)

    # No retry — single attempt
    assert len(calls) == 1


def test_call_openai_does_not_escalate_on_content_filter():
    """Content filter / refusal must not trigger budget escalation."""
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[
                _empty_choice(finish="content_filter", refusal="policy")
            ])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    with pytest.raises(RuntimeError) as exc:
        _call_openai(client, "gpt-5.4", "prompt", 0.4, 16000)

    assert len(calls) == 1
    assert "finish_reason=content_filter" in str(exc.value)
    assert "refusal=policy" in str(exc.value)


def test_llm_call_bumps_starting_budget_for_reasoning_models():
    """llm_call should give reasoning models at least 32K headroom."""
    from llm_backend import llm_call
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_ok_choice()])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))
    backend = {"type": "openai", "client": client}

    # Caller passes default 16000, but reasoning models should be
    # bumped to 32000 starting budget.
    llm_call(backend, "gpt-5.4", "prompt", max_tokens=16000)
    assert calls[0]["max_completion_tokens"] == 32000

    # Caller-provided value above 32000 is respected as-is.
    calls.clear()
    llm_call(backend, "gpt-5.4", "prompt", max_tokens=48000)
    assert calls[0]["max_completion_tokens"] == 48000

    # Legacy models are NOT bumped.
    calls.clear()
    llm_call(backend, "gpt-4o-mini", "prompt", max_tokens=16000)
    assert calls[0]["max_tokens"] == 16000



def test_call_codex_surfaces_useful_stderr_tail(monkeypatch):
    failure = SimpleNamespace(
        returncode=1,
        stderr=(
            "OpenAI Codex v0.30.0\n"
            "--------\n"
            "workdir: /home/mcgrof/devel/ai-post-transformers\n"
            "model: gpt-5.4\n"
            "provider: openai\n"
            "approval: never\n"
            "sandbox: read-only\n"
            "reasoning effort: none\n"
            "session id: 019d5435-24b9-71d2-836d-ebaf47e60dcf\n"
            "--------\n"
            "user\n"
            "Return exactly {\"ok\":true}\n"
            "You've hit your usage limit. Please try again later.\n"
        ),
        stdout="",
    )
    monkeypatch.setattr("llm_backend.subprocess.run", lambda *a, **k: failure)

    with pytest.raises(RuntimeError) as exc:
        _call_codex("gpt-5.4", 'Return exactly {"ok":true}', 200)

    msg = str(exc.value)
    assert "Codex CLI error" in msg
    assert "You've hit your usage limit" in msg
    assert "sandbox: read-only" in msg


def test_call_openai_none_content_gives_clear_error():
    """OpenAI reasoning models can return None content on refusal/filter."""
    class _Create:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=None, refusal="content policy"),
                    finish_reason="content_filter",
                )]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    with pytest.raises(RuntimeError) as exc:
        _call_openai(client, "gpt-5.4", "test prompt", 0.4, 100)

    msg = str(exc.value)
    assert "empty content" in msg
    assert "finish_reason=content_filter" in msg
    assert "refusal=content policy" in msg


def test_call_openai_empty_string_content_gives_clear_error():
    """Empty string content should also produce a clear error."""
    class _Create:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="", refusal=None),
                    finish_reason="stop",
                )]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    with pytest.raises(RuntimeError) as exc:
        _call_openai(client, "gpt-5.4", "test prompt", 0.4, 100)

    msg = str(exc.value)
    assert "empty content" in msg
    assert "finish_reason=stop" in msg


def test_parse_json_empty_output_gives_clear_error():
    with pytest.raises(RuntimeError) as exc:
        _parse_json("", {"type": "openai", "client": object()}, "gpt-5.4", "prompt", 0.4, 100)

    assert "empty output" in str(exc.value)


def test_parse_json_retry_empty_output_gives_clear_error(monkeypatch):
    monkeypatch.setattr("llm_backend._call_openai", lambda *a, **k: "")

    with pytest.raises(RuntimeError) as exc:
        _parse_json("not json", {"type": "openai", "client": object()}, "gpt-5.4", "prompt", 0.4, 100)

    msg = str(exc.value)
    assert "All JSON parse attempts failed" in msg or "empty output" in msg
