import sys
from types import SimpleNamespace

import pytest

from llm_backend import _call_codex, _call_openai, get_llm_backend


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


def test_call_openai_uses_max_completion_tokens_for_gpt5_models():
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    raw = _call_openai(client, 'gpt-5.4', 'Return exactly {"ok": true}', 0.4, 123)

    assert raw == '{"ok": true}'
    assert calls[0]['max_completion_tokens'] == 123
    assert 'max_tokens' not in calls[0]



def test_call_openai_uses_max_tokens_for_legacy_models():
    calls = []

    class _Create:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create()))

    raw = _call_openai(client, 'gpt-4o-mini', 'Return exactly {"ok": true}', 0.4, 77)

    assert raw == '{"ok": true}'
    assert calls[0]['max_tokens'] == 77
    assert 'max_completion_tokens' not in calls[0]



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
