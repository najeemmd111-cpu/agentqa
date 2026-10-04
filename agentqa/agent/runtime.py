"""Executes one scenario over HTTP. Shared by the in-process engine and the
generated pytest suite, so both report identical results."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .models import Result, Scenario


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("{") and value.endswith("}") and value[1:-1] in variables:
        return variables[value[1:-1]]  # keep the captured type (int ids stay ints)
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, variables) for v in value]
    return value


def _render_path(path: str, params: dict[str, Any]) -> str:
    for name, value in params.items():
        path = path.replace("{" + name + "}", str(value))
    return path


def _send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One retry on transport errors: after a server crash the pooled keep-alive
    connection is often closed, and the next request would otherwise fail spuriously."""
    try:
        return client.request(method, url, **kwargs)
    except httpx.TransportError:
        return client.request(method, url, **kwargs)


def run_scenario(client: httpx.Client, scenario: Scenario, auth_header: str | None) -> Result:
    auth = {"Authorization": auth_header} if auth_header else {}
    variables: dict[str, Any] = {}
    try:
        for step in scenario.setup:
            body = _substitute(step.body, variables)
            r = _send(client, step.method, _render_path(step.path, variables), json=body, headers=auth)
            if r.status_code >= 400:
                return Result(
                    scenario=scenario,
                    passed=False,
                    status_code=r.status_code,
                    error=f"setup {step.method} {step.path} failed with {r.status_code}",
                    response_excerpt=r.text[:300],
                )
            data = r.json() if r.content else {}
            for var, key in step.capture.items():
                variables[var] = data.get(key) if isinstance(data, dict) else None

        params = _substitute(scenario.path_params, variables)
        path = _render_path(scenario.path, params)
        kwargs: dict[str, Any] = {
            "params": _substitute(scenario.query, variables) or None,
            "headers": {**(auth if scenario.use_auth else {}), **scenario.headers},
        }
        if scenario.send_body:
            kwargs["json"] = _substitute(scenario.body, variables)

        started = time.perf_counter()
        resp = _send(client, scenario.method, path, **kwargs)
        latency = (time.perf_counter() - started) * 1000
    except httpx.HTTPError as exc:
        return Result(scenario=scenario, passed=False, error=f"transport error: {exc}")

    code = resp.status_code
    passed = code < 500 if scenario.forbid_5xx_only else code in scenario.expected_status
    excerpt = resp.text[:300]
    try:
        excerpt = json.dumps(resp.json())[:300]
    except ValueError:
        pass
    return Result(scenario=scenario, passed=passed, status_code=code, latency_ms=round(latency, 1), response_excerpt=excerpt)
