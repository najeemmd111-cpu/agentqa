import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from agentqa.agent.spec_loader import load_spec, parse_operations
from demo_target.app import app as demo_app
from demo_target.app import reset

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "demo_target" / "openapi.yaml"
AUTH = "Bearer demo-token"


@pytest.fixture
def spec_path() -> Path:
    return SPEC


@pytest.fixture
def operations():
    return parse_operations(load_spec(SPEC))


@pytest.fixture
def demo_client():
    reset()
    with TestClient(demo_app, raise_server_exceptions=False, base_url="http://demo") as c:
        yield c


@pytest.fixture(scope="session")
def live_demo():
    """The buggy demo API served over real HTTP on a free port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(demo_app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


class FakeLLM:
    """Deterministic stand-in for Claude/Groq: answers each prompt type the agent uses."""

    name = "fake"

    def __init__(self):
        self.calls: list[str] = []

    def complete(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        self.calls.append(system)
        if "senior QA engineer" in system:
            if "Operation: POST /orders" in prompt:
                return (
                    "Here you go:\n```json\n"
                    + json.dumps(
                        [
                            {
                                "name": "Ordering more than the available stock is refused",
                                "category": "business",
                                "contract_basis": "must not exceed available stock",
                                "body": {"item_id": "{item_id}", "quantity": 26},
                                "query": {},
                                "path_params": {},
                                "use_auth": True,
                                "expected_status": [409],
                                "rationale": "Setup item has quantity 25; the contract says 409 on insufficient stock",
                            }
                        ]
                    )
                    + "\n```"
                )
            return "[]"
        if "investigating a failing API test" in system:
            return json.dumps({"probes": [{"label": "repeat as-is"}], "hypothesis": "deterministic bug"})
        if "Summarise what the probes prove" in system:
            return "The failure reproduces deterministically."
        if "QA lead" in system:
            return "LLM summary: fix the stock check and the auth bypass first."
        if "Classify the document" in system:
            return '{"label": "confidential", "confidence": 0.91}'
        return "{}"


@pytest.fixture
def fake_llm():
    return FakeLLM()
