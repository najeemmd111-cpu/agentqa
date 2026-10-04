import json
import time

from fastapi.testclient import TestClient

from agentqa.api import app
from agentqa.cli import main

from .conftest import AUTH, SPEC

client = TestClient(app)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_classify_and_extract_endpoints():
    r = client.post("/classify", json={"texts": ["Cour de cassation, Code civil, Paris"], "preset": "jurisdiction"})
    assert r.status_code == 200 and r.json()[0]["label"] == "France"
    assert client.post("/classify", json={"texts": ["x"], "preset": "nope"}).status_code == 422
    r = client.post("/extract", json={"text": "Mail ops@example.com before 2026-01-31"})
    assert r.json()["emails"] == ["ops@example.com"]


def test_run_lifecycle(live_demo):
    r = client.post("/runs", json={"spec": str(SPEC), "base_url": live_demo, "auth_header": AUTH, "llm": "offline"})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    for _ in range(100):
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] != "running":
            break
        time.sleep(0.1)
    assert body["status"] == "done", body
    assert body["report"]["failed"] >= 4
    md = client.get(f"/runs/{run_id}/report.md")
    assert md.status_code == 200 and "# AgentQA report" in md.text
    assert client.get("/runs/doesnotexist").status_code == 404


def test_cli_run_and_ci_gate(live_demo, tmp_path):
    code = main(
        [
            "run",
            "--spec",
            str(SPEC),
            "--base-url",
            live_demo,
            "--auth",
            AUTH,
            "--llm",
            "offline",
            "--out",
            str(tmp_path),
            "--fail-on",
            "critical",
        ]
    )
    assert code == 1  # the auth bypass is critical → CI gate fails
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["findings"][0]["severity"] == "critical"
    assert (tmp_path / "report.md").exists()


def test_cli_generate_exports_pytest_suite(tmp_path):
    assert main(["generate", "--spec", str(SPEC), "--base-url", "http://x", "--llm", "offline", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "test_agentqa_generated.py").exists()
    assert len(json.loads((tmp_path / "test_agentqa_generated.json").read_text())) > 30
