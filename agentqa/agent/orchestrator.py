"""The agent loop: plan → prioritise → act → observe → investigate → explain."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from .executor import HttpxEngine, PytestEngine
from .generator import ScenarioGenerator
from .investigator import Investigator
from .llm import LLMClient, make_llm
from .models import RunReport
from .risk import RiskPrioritizer
from .spec_loader import load_spec, parse_operations
from .summarizer import build_findings, summarize

log = logging.getLogger("agentqa")


class AgentQA:
    def __init__(
        self,
        llm: LLMClient | None = None,
        prioritizer: RiskPrioritizer | None = None,
        investigate: bool = True,
        max_scenarios: int | None = None,
    ):
        self.llm = llm or make_llm()
        self.prioritizer = prioritizer or RiskPrioritizer()
        self.investigate = investigate
        self.max_scenarios = max_scenarios

    def run(
        self,
        spec: str | Path | dict,
        base_url: str,
        auth_header: str | None = None,
        engine: str = "httpx",
        client: httpx.Client | None = None,
        pytest_dir: Path | None = None,
        before_each: Callable[[], None] | None = None,
        run_id: str | None = None,
    ) -> RunReport:
        started = time.perf_counter()
        run_id = run_id or uuid.uuid4().hex[:8]
        raw_spec = load_spec(spec)
        title = (raw_spec.get("info") or {}).get("title", base_url)

        # 1. Plan
        operations = parse_operations(raw_spec)
        log.info("[plan] %d operations found in %s", len(operations), title)
        generator = ScenarioGenerator(operations, self.llm)
        scenarios = generator.generate()
        llm_issues: list[str] = list(generator.llm_issues)
        log.info("[plan] %d scenarios generated (%d from LLM)", len(scenarios), sum(s.source == "llm" for s in scenarios))

        # 2. Prioritise by risk
        scenarios = self.prioritizer.prioritize(scenarios)
        if self.max_scenarios:
            scenarios = scenarios[: self.max_scenarios]

        # 3. Act + observe
        if before_each:
            before_each()
        if engine == "pytest":
            runner = PytestEngine(base_url, auth_header, pytest_dir)
        else:
            runner = HttpxEngine(base_url, auth_header, client=client)
        results = runner.run(scenarios)
        failures = [r for r in results if not r.passed]
        log.info("[act] %d passed, %d failed", len(results) - len(failures), len(failures))

        # 4. Investigate failures autonomously (always in-process for speed)
        notes: list[str] = []
        notes_by_scenario: dict[str, list[str]] = {}
        if self.investigate and failures:
            probe_engine = HttpxEngine(base_url, auth_header, client=client)
            investigator = Investigator(probe_engine.run, self.llm)
            notes = investigator.investigate(failures)
            notes_by_scenario = investigator.notes_by_scenario
            llm_issues += investigator.llm_issues
            log.info("[investigate] %d probes, %d notes", len(investigator.probe_results), len(notes))

        # 5. Explain
        findings = build_findings(failures, notes_by_scenario)
        summary = summarize(findings, len(results), len(failures), self.llm, llm_issues)
        return RunReport(
            run_id=run_id,
            target=title,
            llm=getattr(self.llm, "name", "custom"),
            total=len(results),
            passed=len(results) - len(failures),
            failed=len(failures),
            results=results,
            findings=findings,
            investigation_notes=notes,
            llm_issues=llm_issues,
            summary=summary,
            duration_s=round(time.perf_counter() - started, 2),
        )
