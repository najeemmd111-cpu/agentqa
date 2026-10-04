"""Markdown rendering of a run."""

from __future__ import annotations

from collections import Counter

from .models import RunReport

ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}


def to_markdown(report: RunReport) -> str:
    lines = [
        f"# AgentQA report — {report.target}",
        "",
        f"*Run `{report.run_id}` · LLM: `{report.llm}` · {report.duration_s:.1f}s*",
        "",
        f"**{report.passed}/{report.total} scenarios passed · {report.failed} failed · {len(report.findings)} findings**",
        "",
    ]
    if report.llm_issues:
        lines += ["> **⚠️ AI issues in this run** — parts of the run fell back to rule-based logic:", ">"]
        lines += [f"> - {i}" for i in report.llm_issues] + [""]
    n_ai = sum(r.scenario.source == "llm" for r in report.results)
    if n_ai:
        lines += [f"*{n_ai} scenarios were proposed by the AI (marked 🤖); each one quotes the contract text it tests.*", ""]
    lines += [
        "## Summary",
        "",
        report.summary,
        "",
    ]
    if report.findings:
        lines += ["## Findings", ""]
        for i, f in enumerate(report.findings, 1):
            lines += [
                f"### {i}. {ICON.get(f.severity, '')} {f.title}",
                f"- **Severity:** {f.severity} · **Risk area:** {f.risk_area}",
                f"- **Likely cause:** {f.likely_cause}",
                f"- **Suggested fix:** {f.suggested_fix}",
                "- **Evidence:**",
            ]
            lines += [f"  - {e}" for e in f.evidence]
            lines.append("")
    if report.investigation_notes:
        lines += ["## Agent investigation log", ""] + [f"- {n}" for n in report.investigation_notes] + [""]

    cov = Counter((r.scenario.risk_area, r.passed) for r in report.results)
    areas = sorted({a for a, _ in cov})
    lines += ["## Coverage by risk area", "", "| Risk area | Passed | Failed |", "|---|---|---|"]
    lines += [f"| {a} | {cov[(a, True)]} | {cov[(a, False)]} |" for a in areas]
    lines += [
        "",
        "## All scenarios (highest risk first)",
        "",
        "| | ID | Scenario | Category | Risk | Status | Expected |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report.results:
        s = r.scenario
        exp = "not 5xx" if s.forbid_5xx_only else ",".join(map(str, s.expected_status))
        name = s.name.replace("|", "/") + (" 🤖" if s.source == "llm" else "")
        lines.append(
            f"| {'✅' if r.passed else '❌'} | `{s.id}` | {name} | {s.category.value} | {s.risk_score:.2f} | {r.status_code} | {exp} |"
        )
    return "\n".join(lines) + "\n"
