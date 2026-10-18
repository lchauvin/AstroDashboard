"""LLM calls for the guiding advisor: one `provider/model-id` string, plain HTTP, keys from the environment.

| provider     | endpoint                         | key                |
|--------------|----------------------------------|--------------------|
| ollama       | {ollama_url}/v1 (local)          | none               |
| ollama_cloud | https://ollama.com/v1            | OLLAMA_API_KEY     |
| openrouter   | https://openrouter.ai/api/v1     | OPENROUTER_API_KEY |
| openai       | https://api.openai.com/v1        | OPENAI_API_KEY     |
| anthropic    | https://api.anthropic.com/v1     | ANTHROPIC_API_KEY  |

Only the first slash splits the provider off, so OpenRouter ids (`openrouter/anthropic/…`) pass
through intact. An `ollama/…` model whose Ollama URL is ollama.com is the cloud API and takes
OLLAMA_API_KEY too. The first four are OpenAI-compatible chat completions. Keys never leave the engine.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

PROVIDERS = {
    "ollama": (None, None),
    "ollama_cloud": ("https://ollama.com/v1", "OLLAMA_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
}

# (model, field) pairs a backend has rejected once; not sent again for the life of the process.
_UNSUPPORTED: set[tuple[str, str]] = set()
EFFORTS = ("none", "low", "medium", "high")
CAPPED_TOKENS = 3000  # with no reasoning the answer is a few hundred tokens; a thinking model gets no cap


class LlmError(RuntimeError):
    pass


def split_model(model: str) -> tuple[str, str]:
    provider, sep, name = (model or "").partition("/")
    provider = provider.strip().lower()
    if not sep or not name.strip():
        raise LlmError(f"Model must look like 'provider/model-id' (e.g. ollama/llama3.1:8b), got {model!r}")
    if provider not in PROVIDERS:
        raise LlmError(f"Unknown provider {provider!r}; use one of {', '.join(PROVIDERS)}")
    return provider, name.strip()


def _endpoint(provider: str, ollama_url: str) -> tuple[str | None, str | None]:
    """(base URL, key variable) for a provider; a local `ollama` URL that is really ollama.com counts as the cloud."""
    if provider == "ollama" and (urlparse(ollama_url).hostname or "").lower().removeprefix("www.") == "ollama.com":
        return PROVIDERS["ollama_cloud"]
    return PROVIDERS[provider]


def key_status(model: str, ollama_url: str = "http://localhost:11434") -> dict:
    """Whether the key the model needs is present (never the key itself)."""
    try:
        provider, _ = split_model(model)
    except LlmError as exc:
        return {"ok": False, "message": str(exc)}
    env = _endpoint(provider, ollama_url)[1]
    if env and not os.environ.get(env, "").strip():
        return {"ok": False, "message": f"{env} is not set (add it to .env next to astrodashboard.toml and restart the engine)"}
    return {"ok": True, "message": f"{env} set" if env else "no key needed"}


def call(model: str, system: str, prompt: str, *, ollama_url: str = "http://localhost:11434",
         effort: str = "none") -> dict[str, Any]:
    """Blocking: run it in a worker thread. Returns {text, latencyMs, model, note}.

    `effort` is the reasoning effort for the OpenAI-compatible providers. "none" caps the answer at
    CAPPED_TOKENS; any other level sends no token cap and waits up to 10 minutes. Anthropic models do
    not think unless asked to, so they ignore it.
    """
    if effort not in EFFORTS:
        raise LlmError(f"Reasoning effort must be one of {', '.join(EFFORTS)}, got {effort!r}")
    provider, name = split_model(model)
    base, env = _endpoint(provider, ollama_url)
    key = os.environ.get(env, "").strip() if env else ""
    if env and not key:
        raise LlmError(f"{env} is not set")
    started = time.monotonic()
    if provider == "anthropic":
        text, note = _anthropic(base, key, name, system, prompt, CAPPED_TOKENS, 120.0)
    else:
        base = base or ollama_url.rstrip("/") + "/v1"
        text, note = _openai_compat(base, key or "ollama", name, system, prompt, effort)
    return {"text": text, "latencyMs": int((time.monotonic() - started) * 1000), "model": model, "note": note}


def _openai_compat(base: str, key: str, model: str, system: str, prompt: str, effort: str) -> tuple[str, str]:
    capped = effort == "none"
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if capped:
        body["max_tokens"] = CAPPED_TOKENS
    # Ask the server to constrain the output to JSON, and pass the chosen reasoning effort. Even a "low"
    # think (mistral-large-4: ~3000 tokens) overruns the cap, so a backend that rejects "none" gets
    # "low" uncapped; one that rejects the chosen level gets no field at all.
    if (model, "response_format") not in _UNSUPPORTED:
        body["response_format"] = {"type": "json_object"}
    ladder = ("none", "low") if capped else (effort,)
    if level := next((e for e in ladder if (model, f"reasoning_effort={e}") not in _UNSUPPORTED), None):
        body["reasoning_effort"] = level
    if body.get("reasoning_effort") != "none":
        body.pop("max_tokens", None)
    for _ in range(2 + len(ladder)):
        timeout_s = 120.0 if "max_tokens" in body else 600.0
        try:
            r = httpx.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout_s)
        except httpx.HTTPError as exc:
            raise LlmError(f"{model}: {type(exc).__name__}: {exc}") from exc
        if r.status_code == 400:
            low = r.text.lower()
            hints = {"response_format": ("response_format", "json_object"), "reasoning_effort": ("reason", "think")}
            rejected = next((f for f in hints if f in body and any(h in low for h in hints[f])), None)
            if rejected == "reasoning_effort":
                _UNSUPPORTED.add((model, f"reasoning_effort={body['reasoning_effort']}"))
                if level := next((e for e in ladder if (model, f"reasoning_effort={e}") not in _UNSUPPORTED), None):
                    body["reasoning_effort"] = level
                else:
                    body.pop("reasoning_effort")
                body.pop("max_tokens", None)  # whatever it does now, it is not reasoning-free
                continue
            if rejected:
                _UNSUPPORTED.add((model, rejected))
                body.pop(rejected)
                continue
        if r.status_code >= 400:
            raise LlmError(f"{model}: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"{model}: unexpected answer shape") from exc
        usage = data.get("usage") or {}
        cap = f"/{body['max_tokens']}" if "max_tokens" in body else ""
        return text, f"finish={choice.get('finish_reason')} tokens={usage.get('completion_tokens')}{cap} effort={body.get('reasoning_effort', 'default')}"
    raise LlmError(f"{model}: request rejected")


def _anthropic(base: str, key: str, model: str, system: str, prompt: str, max_tokens: int, timeout_s: float) -> tuple[str, str]:
    try:
        r = httpx.post(
            f"{base}/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise LlmError(f"{model}: {type(exc).__name__}: {exc}") from exc
    if r.status_code >= 400:
        raise LlmError(f"{model}: HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return text, f"stop={data.get('stop_reason')}"


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_THINK = re.compile(r"<(think|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict | None:
    """One JSON object out of a reply that may carry prose, code fences or a <think> block."""
    if not text:
        return None
    text = _THINK.sub("", text).strip()
    candidates = [text, *(m.group(1).strip() for m in _FENCE.finditer(text))]
    start = text.find("{")
    if start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    for c in candidates:
        try:
            parsed = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
