import numpy as np

from agentqa.agent.embeddings import HashingEmbedder
from agentqa.agent.generator import ScenarioGenerator
from agentqa.agent.models import Category
from agentqa.agent.risk import RiskPrioritizer


def test_embeddings_are_normalised_and_semantic():
    e = HashingEmbedder()
    v = e.embed(["missing token unauthorized", "unauthorized without token", "pagination limit offset"])
    assert np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-5)
    assert v[0] @ v[1] > v[0] @ v[2]


def test_every_scenario_gets_an_area_and_order_is_descending(operations):
    ranked = RiskPrioritizer().prioritize(ScenarioGenerator(operations).generate())
    assert all(s.risk_area for s in ranked)
    scores = [s.risk_score for s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_security_outranks_validation(operations):
    ranked = RiskPrioritizer().prioritize(ScenarioGenerator(operations).generate())
    sec = max(s.risk_score for s in ranked if s.category == Category.SECURITY)
    val = max(s.risk_score for s in ranked if s.category == Category.VALIDATION)
    assert sec > val
    assert ranked[0].category == Category.SECURITY
