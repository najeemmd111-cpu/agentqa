"""Risk-based prioritisation: embed each scenario, assign it to the closest risk
area (via labelled prototype descriptions) and compute a priority score."""

from __future__ import annotations

from .embeddings import Embedder, build_prototypes, default_embedder, nearest_label
from .models import Category, Scenario

RISK_AREAS: dict[str, list[str]] = {
    "authentication & authorization": [
        "request without credentials token is refused unauthorized forbidden",
        "access control bypass missing bearer token admin delete",
        "privilege escalation another user's resource",
    ],
    "data integrity": [
        "stock quantity cannot go negative inventory consistency",
        "order exceeds available stock conflict",
        "delete resource then fetch it, duplicate creation, state transition",
    ],
    "input validation": [
        "missing required field is rejected validation error",
        "wrong type value below minimum above maximum rejected",
        "invalid enum value, empty body, malformed input",
    ],
    "robustness & stability": [
        "oversized string payload never causes server error crash",
        "very long input 500 internal server error",
        "unexpected characters unicode large payload",
    ],
    "core functionality": [
        "valid input succeeds happy path create list get",
        "resource can be fetched after creation",
        "pagination returns items",
    ],
}

AREA_WEIGHT = {
    "authentication & authorization": 1.0,
    "data integrity": 0.9,
    "robustness & stability": 0.8,
    "core functionality": 0.7,
    "input validation": 0.6,
}
METHOD_WEIGHT = {"DELETE": 1.0, "POST": 0.9, "PUT": 0.9, "PATCH": 0.85, "GET": 0.6}
CATEGORY_HINT = {
    Category.SECURITY: "authentication & authorization",
    Category.ROBUSTNESS: "robustness & stability",
}


class RiskPrioritizer:
    def __init__(self, embedder: Embedder | None = None):
        self.embedder = embedder or default_embedder()
        self.prototypes = build_prototypes(self.embedder, RISK_AREAS)

    @staticmethod
    def describe(s: Scenario) -> str:
        return f"{s.method} {s.path} {s.name} {s.category.value} {s.rationale}"

    def prioritize(self, scenarios: list[Scenario]) -> list[Scenario]:
        if not scenarios:
            return scenarios
        vectors = self.embedder.embed([self.describe(s) for s in scenarios])
        for s, (area, sim) in zip(scenarios, nearest_label(vectors, self.prototypes), strict=True):
            area = CATEGORY_HINT.get(s.category, area)  # hard signal beats similarity
            s.risk_area = area
            s.risk_score = round(AREA_WEIGHT[area] * METHOD_WEIGHT.get(s.method, 0.7) * (0.75 + 0.25 * max(sim, 0)), 4)
        return sorted(scenarios, key=lambda s: s.risk_score, reverse=True)
