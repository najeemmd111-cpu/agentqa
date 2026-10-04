"""LLM clients. Anthropic (official SDK), Groq (OpenAI-compatible HTTP) and an
offline no-op client so the whole pipeline runs without API keys."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

import httpx


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, prompt: str, max_tokens: int = 2000) -> str: ...


class OfflineLLM:
    """Signals 'no LLM available'; callers fall back to deterministic logic."""

    name = "offline"

    def complete(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        raise RuntimeError("offline mode: no LLM configured")


RETRY_STATUSES = {429, 500, 502, 503, 504}


def post_with_retry(post, *args, attempts: int = 5, max_wait: float = 60.0, sleep=time.sleep, **kwargs) -> httpx.Response:
    """POST with backoff on rate limits (429) and transient 5xx. Honours Retry-After when present."""
    wait = 2.0
    for attempt in range(attempts):
        resp = post(*args, **kwargs)
        if resp.status_code not in RETRY_STATUSES or attempt == attempts - 1:
            return resp
        retry_after = resp.headers.get("retry-after")
        try:
            delay = float(retry_after) if retry_after else wait
        except ValueError:
            delay = wait
        sleep(min(delay, max_wait))
        wait = min(wait * 2, max_wait)
    return resp


class AnthropicLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic  # imported lazily so offline installs don't need it configured

        self.model = model or os.getenv("AGENTQA_ANTHROPIC_MODEL", "claude-sonnet-5-5")
        # the SDK already retries 429/5xx with backoff; give it more attempts for rate-limited tiers
        self.client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"), max_retries=5)
        self.name = f"anthropic:{self.model}"

    def complete(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        if not text.strip():
            raise RuntimeError("Anthropic returned an empty answer")
        return text


class GroqLLM:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.getenv("AGENTQA_GROQ_MODEL", "openai/gpt-oss-120b")
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self.name = f"groq:{self.model}"

    def complete(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        resp = post_with_retry(
            httpx.post,
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if resp.status_code == 404:
            raise RuntimeError(f"Groq model {self.model!r} not found; set AGENTQA_GROQ_MODEL to a model from /openai/v1/models")
        if resp.status_code == 429:
            raise RuntimeError("Groq rate limit (429) still hit after retries; wait a minute and rerun, or use a paid tier")
        if resp.status_code >= 400:
            raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}".replace("\n", " "))
        content = resp.json()["choices"][0]["message"].get("content") or ""
        if not content.strip():
            raise RuntimeError("Groq returned an empty answer (reasoning models may need a higher max_tokens)")
        return content


def make_llm(provider: str | None = None) -> LLMClient:
    """provider: anthropic | groq | offline | None (auto-detect from env keys)."""
    provider = (provider or os.getenv("AGENTQA_LLM") or "auto").lower()
    if provider == "auto":
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("GROQ_API_KEY"):
            provider = "groq"
        else:
            provider = "offline"
    if provider == "anthropic":
        return AnthropicLLM()
    if provider == "groq":
        return GroqLLM()
    if provider == "offline":
        return OfflineLLM()
    raise ValueError(f"Unknown LLM provider: {provider}")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_list(text: str) -> list:
    """Like parse_json, but also accepts {"scenarios": [...]}-style wrappers that some models return."""
    value = parse_json(text)
    if isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    if not isinstance(value, list):
        raise ValueError("expected a JSON array")
    return value


def parse_json(text: str) -> Any:
    """Extract the first JSON value from an LLM answer (handles ``` fences and prose)."""
    m = _FENCE.search(text)
    candidate = m.group(1) if m else text
    candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start, end = candidate.find(open_c), candidate.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("LLM did not return valid JSON")
