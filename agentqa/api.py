"""FastAPI service exposing the agent and the NLP utilities."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__
from .agent.llm import make_llm
from .agent.models import RunReport
from .agent.orchestrator import AgentQA
from .agent.report import to_markdown
from .nlp import PRESETS, DocumentClassifier, extract_entities

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="AgentQA", version=__version__, description="LLM agent that generates, executes and explains API test scenarios.")

_runs: dict[str, dict[str, Any]] = {}


class RunRequest(BaseModel):
    spec: str | dict = Field(description="OpenAPI document: URL, file path on the server, or inline JSON object")
    base_url: str = Field(examples=["http://localhost:8000"])
    auth_header: str | None = Field(default=None, examples=["Bearer demo-token"])
    llm: str | None = Field(default=None, description="anthropic | groq | offline (default: auto-detect)")
    engine: str = Field(default="httpx", pattern="^(httpx|pytest)$")
    investigate: bool = True
    max_scenarios: int | None = None


class ClassifyRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    preset: str | None = Field(default="confidentiality", description=f"one of {sorted(PRESETS)}")
    labels: dict[str, list[str]] | None = Field(default=None, description="custom label → example descriptions")
    llm: str | None = "offline"


class ExtractRequest(BaseModel):
    text: str
    fields: list[str] | None = None
    llm: str | None = "offline"


def _execute(run_id: str, req: RunRequest) -> None:
    try:
        agent = AgentQA(llm=make_llm(req.llm), investigate=req.investigate, max_scenarios=req.max_scenarios)
        report = agent.run(req.spec, req.base_url, req.auth_header, engine=req.engine, run_id=run_id)
        _runs[run_id].update(status="done", report=report)
    except Exception as exc:  # surface errors to the client instead of losing them in a background task
        _runs[run_id].update(status="error", error=str(exc))


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.post("/runs", status_code=202)
def start_run(req: RunRequest, background: BackgroundTasks):
    run_id = uuid.uuid4().hex[:8]
    _runs[run_id] = {"status": "running"}
    background.add_task(_execute, run_id, req)
    return {"run_id": run_id, "status": "running"}


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    if run["status"] != "done":
        return {"run_id": run_id, **run}
    report: RunReport = run["report"]
    return {"run_id": run_id, "status": "done", "report": report.model_dump(mode="json")}


@app.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
def get_markdown(run_id: str) -> str:
    run = _runs.get(run_id)
    if not run or run["status"] != "done":
        raise HTTPException(404, "report not ready")
    return to_markdown(run["report"])


@app.post("/classify")
def classify(req: ClassifyRequest):
    llm = make_llm(req.llm)
    try:
        clf = DocumentClassifier(req.labels, llm=llm) if req.labels else DocumentClassifier.preset(req.preset or "", llm=llm)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc
    return [{"text": t[:80], **vars(c)} for t, c in zip(req.texts, clf.classify(req.texts), strict=True)]


@app.post("/extract")
def extract(req: ExtractRequest):
    return extract_entities(req.text, make_llm(req.llm), req.fields)
