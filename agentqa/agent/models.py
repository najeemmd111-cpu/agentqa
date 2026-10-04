"""Pydantic models shared across the pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Category(str, Enum):
    FUNCTIONAL = "functional"  # happy path
    VALIDATION = "validation"  # bad / missing / out-of-range input
    BOUNDARY = "boundary"  # exactly-at-limit values that must be accepted
    SECURITY = "security"  # authn / authz
    NOT_FOUND = "not_found"  # unknown resources
    ROBUSTNESS = "robustness"  # oversized / odd input must never 5xx
    BUSINESS = "business"  # domain rules (usually LLM-generated)


class Operation(BaseModel):
    operation_id: str
    method: str
    path: str
    summary: str = ""
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    responses: dict[str, str] = Field(default_factory=dict)  # status -> description
    requires_auth: bool = False

    @property
    def path_params(self) -> list[dict[str, Any]]:
        return [p for p in self.parameters if p.get("in") == "path"]

    @property
    def query_params(self) -> list[dict[str, Any]]:
        return [p for p in self.parameters if p.get("in") == "query"]

    def label(self) -> str:
        return f"{self.method.upper()} {self.path}"


class Step(BaseModel):
    """A setup request whose response values can be captured into variables."""

    method: str
    path: str
    body: Any = None
    capture: dict[str, str] = Field(default_factory=dict)  # var name -> top-level JSON key


class Scenario(BaseModel):
    id: str
    operation_id: str
    name: str
    category: Category
    method: str
    path: str  # may contain {vars}
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    send_body: bool = False
    use_auth: bool = True
    headers: dict[str, str] = Field(default_factory=dict)  # extra headers (e.g. a deliberately wrong token)
    setup: list[Step] = Field(default_factory=list)
    expected_status: list[int] = Field(default_factory=list)
    forbid_5xx_only: bool = False  # robustness: anything but 5xx passes
    rationale: str = ""
    source: str = "rules"  # rules | llm | investigator
    risk_area: str | None = None
    risk_score: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict)  # e.g. {"field": "name", "kind": "huge_string"}


class Result(BaseModel):
    scenario: Scenario
    passed: bool
    status_code: int | None = None
    latency_ms: float = 0.0
    response_excerpt: str = ""
    error: str | None = None

    @property
    def failure_reason(self) -> str:
        if self.passed:
            return ""
        if self.error:
            return self.error
        s = self.scenario
        if s.forbid_5xx_only:
            return f"server error {self.status_code}"
        return f"expected {s.expected_status}, got {self.status_code}"


class Finding(BaseModel):
    title: str
    severity: str  # critical | high | medium | low
    operation: str
    risk_area: str | None = None
    evidence: list[str] = Field(default_factory=list)
    likely_cause: str = ""
    suggested_fix: str = ""
    scenario_ids: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    run_id: str
    target: str
    llm: str
    total: int
    passed: int
    failed: int
    results: list[Result]
    findings: list[Finding]
    investigation_notes: list[str] = Field(default_factory=list)
    llm_issues: list[str] = Field(default_factory=list)  # AI calls that failed or were discarded
    summary: str = ""
    duration_s: float = 0.0
