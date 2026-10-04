import httpx
import pytest

from agentqa.agent import llm as llm_mod
from agentqa.agent.llm import GroqLLM, parse_json_list, post_with_retry


def _resp(status, body=None, headers=None):
    return httpx.Response(status, json=body or {}, headers=headers or {}, request=httpx.Request("POST", "https://x"))


def test_retries_rate_limit_then_succeeds():
    calls, sleeps = [], []
    answers = [_resp(429, headers={"retry-after": "3"}), _resp(503), _resp(200, {"ok": True})]

    def post(*a, **k):
        calls.append(1)
        return answers[len(calls) - 1]

    r = post_with_retry(post, "u", sleep=sleeps.append)
    assert r.status_code == 200 and len(calls) == 3
    assert sleeps == [3.0, 4.0]  # Retry-After honoured, then exponential backoff


def test_gives_up_after_attempts():
    sleeps = []
    r = post_with_retry(lambda *a, **k: _resp(429), "u", attempts=3, sleep=sleeps.append)
    assert r.status_code == 429 and len(sleeps) == 2


def test_groq_errors_are_single_line(monkeypatch):
    monkeypatch.setattr(llm_mod, "post_with_retry", lambda *a, **k: _resp(429))
    with pytest.raises(RuntimeError, match="rate limit") as exc:
        GroqLLM(api_key="k").complete("s", "p")
    assert "\n" not in str(exc.value)

    monkeypatch.setattr(llm_mod, "post_with_retry", lambda *a, **k: _resp(200, {"choices": [{"message": {"content": ""}}]}))
    with pytest.raises(RuntimeError, match="empty answer"):
        GroqLLM(api_key="k").complete("s", "p")


def test_parse_json_list_unwraps_objects():
    assert parse_json_list('{"scenarios": [1]}') == [1]
    with pytest.raises(ValueError):
        parse_json_list('{"a": 1}')
