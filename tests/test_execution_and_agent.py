from agentqa.agent.executor import HttpxEngine, PytestEngine
from agentqa.agent.generator import ScenarioGenerator
from agentqa.agent.investigator import Investigator
from agentqa.agent.llm import OfflineLLM
from agentqa.agent.models import Category, Scenario
from agentqa.agent.orchestrator import AgentQA
from agentqa.agent.report import to_markdown
from agentqa.agent.runtime import _substitute

from .conftest import AUTH


def test_substitution_keeps_types():
    assert _substitute({"a": "{id}", "b": ["{id}", "x{id}"]}, {"id": 7}) == {"a": 7, "b": [7, "x{id}"]}


def test_happy_path_passes_against_demo(demo_client):
    s = Scenario(
        id="t1", operation_id="health", name="health", category=Category.FUNCTIONAL, method="GET", path="/health", expected_status=[200]
    )
    [r] = HttpxEngine("http://demo", client=demo_client).run([s])
    assert r.passed and r.status_code == 200


def test_offline_agent_finds_the_four_rule_detectable_bugs(spec_path, demo_client):
    report = AgentQA(llm=OfflineLLM()).run(spec_path, "http://demo", AUTH, client=demo_client)
    titles = [f.title for f in report.findings]
    assert report.total > 30 and report.failed >= 4
    assert "Endpoint does not enforce authentication: DELETE /items/{item_id}" in titles  # BUG-4
    assert "Server crashes (5xx) on unexpected input: POST /items" in titles  # BUG-2
    assert "Unknown resources do not return 404: GET /items/{item_id}" in titles  # BUG-3
    assert "Invalid input is accepted: POST /items" in titles  # BUG-1
    assert report.findings[0].severity == "critical"  # sorted by severity
    md = to_markdown(report)
    assert "## Findings" in md and "## Coverage by risk area" in md


def test_investigator_bisects_crash_threshold(spec_path, demo_client, operations):
    scenarios = ScenarioGenerator(operations).generate()
    huge = next(s for s in scenarios if s.meta.get("kind") == "huge_string" and s.meta["field"] == "name")
    engine = HttpxEngine("http://demo", AUTH, client=demo_client)
    [failure] = engine.run([huge])
    assert failure.status_code == 500
    inv = Investigator(engine.run)
    inv.investigate([failure])
    assert "from 121 characters" in inv.notes[0]
    assert inv.notes_by_scenario[huge.id] == inv.notes


def test_llm_agent_also_finds_business_rule_bug(spec_path, demo_client, fake_llm):
    report = AgentQA(llm=fake_llm).run(spec_path, "http://demo", AUTH, client=demo_client)
    business = [f for f in report.findings if f.title.startswith("Business rule not enforced")]
    assert business and business[0].operation == "POST /orders"  # BUG-5
    assert report.summary.startswith("LLM summary")
    assert any("reproduces deterministically" in n for n in report.investigation_notes)
    assert any("from 121 characters" in n for n in report.investigation_notes)  # precise probes kept in AI mode


def test_pytest_engine_matches_httpx_engine(spec_path, live_demo, operations, tmp_path):
    scenarios = ScenarioGenerator(operations).generate()[:12]
    via_pytest = PytestEngine(live_demo, AUTH, tmp_path).run(scenarios)
    assert (tmp_path / "test_agentqa_generated.py").exists()
    assert (tmp_path / "junit.xml").exists()
    via_httpx = HttpxEngine(live_demo, AUTH).run(scenarios)
    assert [r.passed for r in via_pytest] == [r.passed for r in via_httpx]
