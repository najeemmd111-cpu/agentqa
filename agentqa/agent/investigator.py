"""The agentic part: after the first execution pass, the agent looks at each
failure and autonomously runs follow-up probes to confirm it and narrow it down
(e.g. bisecting the exact input length that crashes the server).

With an LLM configured, the LLM proposes the probes; offline, deterministic
strategies are used. Every probe is a real request against the target."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from .llm import LLMClient, OfflineLLM, parse_json
from .models import Result, Scenario

log = logging.getLogger(__name__)

RunFn = Callable[[list[Scenario]], list[Result]]


class Investigator:
    def __init__(self, run: RunFn, llm: LLMClient | None = None, max_probes_per_failure: int = 14):
        self.run = run
        self.llm = llm or OfflineLLM()
        self.max_probes = max_probes_per_failure
        self.notes: list[str] = []
        self.notes_by_scenario: dict[str, list[str]] = {}
        self.llm_issues: list[str] = []
        self.probe_results: list[Result] = []

    # ---------------------------------------------------------------- utils
    def _probe(self, base: Scenario, suffix: str, **changes) -> Result:
        n = len(self.probe_results) + 1
        s = base.model_copy(
            deep=True, update={"id": f"{base.id}-probe{n}", "name": f"{base.name} [{suffix}]", "source": "investigator", **changes}
        )
        result = self.run([s])[0]
        self.probe_results.append(result)
        return result

    def _with_field(self, base: Scenario, field: str, value) -> dict:
        body = dict(base.body or {})
        body[field] = value
        return {"body": body}

    # ---------------------------------------------------------------- main
    def investigate(self, failures: list[Result]) -> list[str]:
        """Deterministic strategies first (they are precise, e.g. bisection); the LLM only
        investigates failures no strategy covers."""
        use_llm = not isinstance(self.llm, OfflineLLM)
        for f in failures:
            before = len(self.notes)
            try:
                self._heuristic_probe(f)
            except Exception as exc:
                log.warning("heuristic investigation of %s failed: %s", f.scenario.id, exc)
            if use_llm and len(self.notes) == before:
                try:
                    self._llm_probe(f)
                except Exception as exc:
                    log.warning("LLM investigation of %s failed: %s", f.scenario.id, exc)
                    self.llm_issues.append(f"Investigation of {f.scenario.id} failed: {exc}")
            self.notes_by_scenario[f.scenario.id] = self.notes[before:]
        return self.notes

    def _heuristic_probe(self, f: Result) -> None:
        s, kind = f.scenario, f.scenario.meta.get("kind")
        op = f"{s.method} {s.path}"

        if kind == "huge_string" and f.status_code and f.status_code >= 500:
            field = s.meta["field"]
            lo, hi = int(s.meta.get("low", 1)), len(s.body[field])  # lo: assumed OK, hi: known crash
            probes = 0
            while hi - lo > 1 and probes < self.max_probes:
                mid = (lo + hi) // 2
                r = self._probe(s, f"len={mid}", **self._with_field(s, field, "A" * mid))
                probes += 1
                if r.status_code and r.status_code >= 500:
                    hi = mid
                else:
                    lo = mid
            self.notes.append(
                f"{op}: '{field}' crashes the server from {hi} characters "
                f"(≤{lo} is handled) — found by bisection in {probes} probes. "
                f"Looks like an unvalidated column-size limit."
            )

        elif kind in ("below_min", "above_max") and f.status_code and f.status_code < 300:
            field, limit, step = s.meta["field"], s.meta["limit"], s.meta["step"]
            sign = -1 if kind == "below_min" else 1
            extremes = [limit + sign * step * 100, limit + sign * 10**6]
            accepted = [
                v
                for v in extremes
                if (r := self._probe(s, f"{field}={v}", **self._with_field(s, field, v))).status_code and r.status_code < 300
            ]
            verdict = "every probed value was accepted" if len(accepted) == len(extremes) else "only some values were accepted"
            self.notes.append(
                f"{op}: '{field}' {'minimum' if sign < 0 else 'maximum'} {limit} is not enforced — "
                f"{verdict} ({', '.join(map(str, extremes))})."
            )

        elif kind == "too_long" and f.status_code and f.status_code < 300:
            self.notes.append(
                f"{op}: '{s.meta['field']}' maxLength {s.meta['limit']} is not validated (server accepted {s.meta['limit'] + 1} chars)."
            )

        elif kind == "no_auth" and f.status_code and f.status_code < 300:
            r = self._probe(s, "wrong token", use_auth=False, headers={"Authorization": "Bearer wrong-token"})
            ignores_token = r.status_code is not None and r.status_code < 300
            self.notes.append(
                f"{op}: accepts requests without credentials (status {f.status_code}); "
                f"with a wrong token it returned {r.status_code} — "
                f"{'the token is not checked at all' if ignores_token else 'only the missing-token case is unhandled'}."
            )

        elif kind == "unknown_id" and f.status_code and f.status_code < 300:
            other = {k: 123456789 for k in s.path_params}
            r = self._probe(s, "another unknown id", path_params=other)
            self.notes.append(
                f"{op}: unknown ids return {f.status_code} instead of 404 "
                f"(confirmed with a second id → {r.status_code}); body was {f.response_excerpt[:60]!r}."
            )

        elif f.status_code and f.status_code >= 500:
            r = self._probe(s, "retry")
            flaky = r.status_code is not None and r.status_code < 500
            self.notes.append(
                f"{op}: '{s.name}' returned {f.status_code}{' once but passed on retry (flaky)' if flaky else ' consistently'}."
            )

    def _llm_probe(self, f: Result) -> None:
        s = f.scenario
        system = (
            "You are a QA engineer investigating a failing API test. Propose follow-up requests that "
            "confirm the bug and narrow down its exact trigger. JSON only."
        )
        prompt = f"""Failing scenario: {json.dumps(s.model_dump(mode="json", exclude={"setup"}))}
Observed: status {f.status_code}, body {f.response_excerpt!r}, reason: {f.failure_reason}
Return {{"probes": [{{"label": str, "body": object|null, "query": object, "path_params": object,
"use_auth": bool, "expected_status": [int]}}] (max 4), "hypothesis": str}}"""
        plan = parse_json(self.llm.complete(system, prompt, max_tokens=3000))
        if not isinstance(plan, dict):
            raise ValueError("expected a JSON object with 'probes'")
        observations = []
        for p in (plan.get("probes") or [])[:4]:
            changes = {k: p[k] for k in ("body", "query", "use_auth") if k in p}
            # path params: only integers or the original placeholder, otherwise the probe tests the router, not the bug
            pp = {
                k: v
                for k, v in (p.get("path_params") or {}).items()
                if k in s.path_params and (isinstance(v, int) or str(v).isdigit() or v == s.path_params[k])
            }
            if pp:
                changes["path_params"] = {**s.path_params, **pp}
            if p.get("expected_status"):
                changes["expected_status"] = [int(c) for c in p["expected_status"]]
            r = self._probe(s, p.get("label", "probe"), **changes)
            observations.append(f"{p.get('label', 'probe')} → {r.status_code}")
        verdict = self.llm.complete(
            "Summarise what the probes prove about the bug in one sentence.",
            f"Hypothesis: {plan.get('hypothesis')}\nProbes: {observations}\nOriginal failure: {f.failure_reason}",
            max_tokens=1500,
        )
        self.notes.append(f"{s.method} {s.path}: {verdict.strip()} (probes: {'; '.join(observations)})")
