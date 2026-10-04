"""Command line entry point:  python -m agentqa.cli run --spec ... --base-url ..."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .agent.executor import export_pytest
from .agent.generator import ScenarioGenerator
from .agent.llm import make_llm
from .agent.orchestrator import AgentQA
from .agent.report import to_markdown
from .agent.risk import RiskPrioritizer
from .agent.spec_loader import load_spec, parse_operations


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentqa", description="LLM agent for API test generation & execution")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="generate, execute, investigate and report")
    run.add_argument("--spec", required=True, help="OpenAPI file path or URL")
    run.add_argument("--base-url", required=True)
    run.add_argument("--auth", default=None, help='e.g. "Bearer demo-token"')
    run.add_argument("--llm", default=None, help="anthropic | groq | offline (default: auto)")
    run.add_argument("--engine", choices=["httpx", "pytest"], default="httpx")
    run.add_argument("--no-investigate", action="store_true")
    run.add_argument("--max-scenarios", type=int)
    run.add_argument("--out", default="agentqa-report", help="output directory")
    run.add_argument(
        "--fail-on",
        choices=["never", "critical", "high", "medium", "any"],
        default="never",
        help="exit 1 if a finding of this severity or worse exists (for CI gates)",
    )

    gen = sub.add_parser("generate", help="only generate scenarios and export a pytest suite")
    gen.add_argument("--spec", required=True)
    gen.add_argument("--base-url", required=True)
    gen.add_argument("--auth", default=None)
    gen.add_argument("--llm", default=None)
    gen.add_argument("--out", default="generated_tests")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "generate":
        ops = parse_operations(load_spec(args.spec))
        scenarios = RiskPrioritizer().prioritize(ScenarioGenerator(ops, make_llm(args.llm)).generate())
        path = export_pytest(scenarios, Path(args.out), args.base_url, args.auth)
        print(f"{len(scenarios)} scenarios → {path}")
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    agent = AgentQA(llm=make_llm(args.llm), investigate=not args.no_investigate, max_scenarios=args.max_scenarios)
    report = agent.run(
        args.spec, args.base_url, args.auth, engine=args.engine, pytest_dir=out / "pytest" if args.engine == "pytest" else None
    )
    (out / "report.md").write_text(to_markdown(report), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    print(f"\n{report.passed}/{report.total} passed, {len(report.findings)} findings → {out / 'report.md'}\n")
    print(report.summary)

    order = ["critical", "high", "medium", "low"]
    if args.fail_on != "never":
        limit = len(order) if args.fail_on == "any" else order.index(args.fail_on) + 1
        if any(f.severity in order[:limit] for f in report.findings):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
