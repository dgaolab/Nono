import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts")))
from lib import llm


class _Resp(io.BytesIO):
    """A BytesIO that doubles as a context manager, like urlopen's return."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener_returning(payload):
    def opener(req, timeout=None):
        return _Resp(json.dumps(payload).encode("utf-8"))
    return opener


def test_unavailable_is_runtimeerror():
    assert issubclass(llm.LLMUnavailable, RuntimeError)


def test_chat_returns_assistant_text():
    opener = _opener_returning({"choices": [{"message": {"content": "hello there"}}]})
    out = llm.chat([{"role": "user", "content": "hi"}], base_url="http://x/v1", _opener=opener)
    assert out == "hello there"


def test_chat_raises_on_endpoint_failure():
    def boom(req, timeout=None):
        raise OSError("connection refused")

    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "hi"}], _opener=boom)


def test_chat_raises_on_unexpected_shape():
    opener = _opener_returning({"unexpected": True})
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "hi"}], base_url="http://x/v1", _opener=opener)


def test_config_prefers_explicit_then_env_then_default(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert llm._config(None, None, None) == (llm.DEFAULT_BASE_URL, llm.DEFAULT_MODEL, "not-needed")

    monkeypatch.setenv("LLM_BASE_URL", "http://env/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    assert llm._config(None, None, None)[:2] == ("http://env/v1", "env-model")

    # explicit args win over environment
    assert llm._config("http://explicit/v1", "explicit-model", "key")[0] == "http://explicit/v1"
