#!/usr/bin/env python3
"""LLM seam — the single place nono-librarian talks to a model.

Targets any OpenAI-compatible ``/chat/completions`` endpoint. In the scheduled
setup the local open-weight model is served by vLLM; the seam also works with
llama.cpp, LM Studio, or Ollama's OpenAI shim. It uses only stdlib ``urllib``,
so importing this module pulls in no dependency and adds nothing to the env.

There is no model discovery: the scheduling agent (Claude or the local Hermes
agent) is already bound to a model and injects the endpoint by environment at
launch. The same code therefore runs against whatever server it is pointed at:

  LLM_BASE_URL  base URL of the OpenAI-compatible server
                (default "http://localhost:8000/v1" — vLLM's default port)
  LLM_MODEL     model name to request — must match vLLM's --served-model-name
                (default "Qwen/Qwen2.5-7B-Instruct")
  LLM_API_KEY   bearer token if the server wants one (default "not-needed";
                local servers usually ignore it)

The defaults are only a fallback for an interactive session with a local vLLM
server up; scheduled runs override all three. Do not hardcode a real
``LLM_API_KEY`` here — the agent injects it per run.

`chat` returns the assistant message text, or raises `LLMUnavailable` on any
connection / HTTP / parse failure. Callers are expected to catch that and
degrade to deterministic output — the same graceful-fallback philosophy as the
embeddings seam, where a missing model never breaks the workflow.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"


class LLMUnavailable(RuntimeError):
    """Raised when the local model endpoint cannot be reached or used."""


def _config(base_url, model, api_key):
    """Resolve effective settings: explicit arg > environment > built-in default."""
    return (
        base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
        model or os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        api_key or os.environ.get("LLM_API_KEY", "not-needed"),
    )


def chat(messages, *, model=None, base_url=None, api_key=None,
         temperature=0.2, max_tokens=1024, timeout=120,
         _opener=urllib.request.urlopen):
    """POST an OpenAI-style chat completion and return the reply text.

    ``messages`` is a list of ``{"role": ..., "content": ...}`` dicts. Any
    failure (endpoint down, non-JSON body, unexpected shape) is normalized to
    `LLMUnavailable` so callers have a single thing to catch. ``_opener`` is an
    injection seam for tests; production passes the real ``urlopen``.
    """
    base_url, model, api_key = _config(base_url, model, api_key)
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with _opener(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LLMUnavailable(f"local model endpoint failed at {url}: {e}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMUnavailable(f"unexpected response shape from {url}: {e}") from e


def extract_json_object(text):
    """Parse the first balanced ``{...}`` object out of a model reply.

    Tolerates code fences and surrounding prose. Raises ``ValueError`` if no
    parseable JSON object is present.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model reply JSON was not an object")
    return obj
