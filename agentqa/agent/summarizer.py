"""Turns raw failures into grouped findings and a plain-language summary."""

from __future__ import annotations

import json
from collections import defaultdict

from .llm import LLMClient, OfflineLLM
from .models import Category, Finding, Result

KIND_INFO = {
    "missing_auth": (
        "Endpoint does not enforce authentication",
        "critical",
        "The handler never validates the Authorization header.",
        "Add the same auth dependency/guard used by the other protected endpoints and a regression test.",
    ),
    "server_error": (
        "Server crashes (5xx) on unexpected input",
        "high",
        "An unhandled exception escapes the handler, typically a storage constraint hit before validation.",
        "Validate the input at the API boundary (e.g. max length) and map storage errors to 4xx.",
    ),
    "business_rule": (
        "Business rule not enforced",
        "high",
        "Domain invariant (e.g. stock level) is not checked before mutating state.",
        "Check the invariant inside the same transaction as the write and return the documented error code.",
    ),
    "accepted_invalid": (
        "Invalid input is accepted",
        "medium",
        "Validation constraints from the contract are missing in the request model.",
        "Add the missing constraints (minimum / maximum / maxLength / enum) to the request schema.",
    ),
    "wrong_not_found": (
        "Unknown resources do not return 404",
        "medium",
        "The handler returns the lookup result even when it is empty.",
        "Raise a 404 when the resource does not exist.",
    ),
    "rejected_valid": (
        "Valid input is rejected",
        "medium",
        "Validation is stricter than the contract, or setup data is wrong.",
        "Align the validation rules with the documented contract.",
    ),
    "contract_mismatch": (
        "Response does not match the contract",
        "low",
        "Status code differs from the documented responses.",
        "Align the implementation or update the contract.",
    ),
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def classify_failure(r: Result) -> str:
    s, code = r.scenario, r.status_code or 0
    if code >= 500:
        return "server_error"
    if s.category == Category.SECURITY and code < 300:
        return "missing_auth"
    if s.category == Category.BUSINESS:
        return "business_rule"
    if s.category == Category.NOT_FOUND and code < 300:
        return "wrong_not_found"
    if s.category == Category.VALIDATION and code < 300:
        return "accepted_invalid"
    if s.category in (Category.FUNCTIONAL, Category.BOUNDARY) and 400 <= code < 500:
        return "rejected_valid"
    return "contract_mismatch"


def build_findings(failures: list[Result], notes_by_scenario: dict[str, list[str]] | None = None) -> list[Finding]:
    notes_by_scenario = notes_by_scenario or {}
    groups: dict[tuple[str, str], list[Result]] = defaultdict(list)
    for r in failures:
        groups[(f"{r.scenario.method} {r.scenario.path}", classify_failure(r))].append(r)

    findings = []
    for (operation, kind), results in groups.items():
        title, severity, cause, fix = KIND_INFO[kind]
        evidence = [f"{r.scenario.id}: {r.scenario.name} → {r.failure_reason}" for r in results]
        evidence += [n for r in results for n in notes_by_scenario.get(r.scenario.id, [])]
        findings.append(
            Finding(
                title=f"{title}: {operation}",
                severity=severity,
                operation=operation,
                risk_area=results[0].scenario.risk_area,
                evidence=evidence,
                likely_cause=cause,
                suggested_fix=fix,
                scenario_ids=[r.scenario.id for r in results],
            )
        )
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.operation))


def summarize(findings: list[Finding], total: int, failed: int, llm: LLMClient | None = None, issues: list[str] | None = None) -> str:
    llm = llm or OfflineLLM()
    if not isinstance(llm, OfflineLLM):
        try:
            return llm.complete(
                "You are a QA lead writing for developers and product managers. Plain language, no jargon, "
                "no markdown headings. 4-8 sentences: overall health, the most important problems first, "
                "and what to fix first.",
                f"{total} scenarios executed, {failed} failed.\nFindings:\n" + json.dumps([f.model_dump() for f in findings], indent=1),
                max_tokens=2500,
            ).strip()
        except Exception as exc:  # fall through to the template, but say so
            if issues is not None:
                issues.append(f"AI summary failed, template used instead: {exc}")
    if not findings:
        return f"All {total} scenarios passed. The API honours its contract for every case the agent tried."
    by_sev = defaultdict(int)
    for f in findings:
        by_sev[f.severity] += 1
    parts = [
        f"{failed} of {total} scenarios failed, grouped into {len(findings)} findings "
        f"({', '.join(f'{n} {s}' for s, n in sorted(by_sev.items(), key=lambda x: SEVERITY_ORDER[x[0]]))})."
    ]
    top = findings[0]
    parts.append(f"Fix first — {top.title}. {top.likely_cause}")
    if len(findings) > 1:
        parts.append("Then: " + "; ".join(f.title for f in findings[1:4]) + ".")
    return " ".join(parts)
