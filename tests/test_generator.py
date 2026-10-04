from agentqa.agent.generator import ScenarioGenerator
from agentqa.agent.llm import parse_json
from agentqa.agent.models import Category
from agentqa.agent.schema_synth import example_for, wrong_type_for


def test_rule_categories(operations):
    scenarios = ScenarioGenerator(operations).generate()
    cats = {s.category for s in scenarios}
    assert {Category.FUNCTIONAL, Category.VALIDATION, Category.BOUNDARY, Category.SECURITY, Category.NOT_FOUND, Category.ROBUSTNESS} <= cats
    names = [s.name for s in scenarios if s.operation_id == "createItem"]
    assert "Missing required field 'price' is rejected" in names
    assert any("below minimum (-1)" in n for n in names)
    assert any("longer than maxLength (101 chars)" in n for n in names)


def test_path_params_get_setup_step(operations):
    s = next(s for s in ScenarioGenerator(operations).generate() if s.operation_id == "getItem" and s.category == Category.FUNCTIONAL)
    assert s.setup[0].method == "POST" and s.setup[0].path == "/items"
    assert s.setup[0].capture == {"item_id": "id"}
    assert s.path_params == {"item_id": "{item_id}"}


def test_foreign_keys_create_referenced_resource(operations):
    s = next(s for s in ScenarioGenerator(operations).generate() if s.operation_id == "createOrder" and s.category == Category.FUNCTIONAL)
    assert s.body["item_id"] == "{item_id}"
    assert any(step.path == "/items" and step.capture == {"item_id": "id"} for step in s.setup)


def test_unique_ids_and_dedupe(operations):
    scenarios = ScenarioGenerator(operations).generate()
    assert len({s.id for s in scenarios}) == len(scenarios)


def test_llm_adds_business_scenarios(operations, fake_llm):
    scenarios = ScenarioGenerator(operations, fake_llm).generate()
    llm = [s for s in scenarios if s.source == "llm"]
    assert len(llm) == 1
    assert llm[0].category == Category.BUSINESS and llm[0].expected_status == [409]


def test_bad_llm_output_does_not_break_generation(operations):
    class Broken:
        name = "broken"

        def complete(self, *a, **k):
            return "I cannot answer in JSON, sorry"

    assert len(ScenarioGenerator(operations, Broken()).generate()) == len(ScenarioGenerator(operations).generate())


def test_schema_synth():
    assert example_for({"type": "integer", "minimum": 5}) == 5
    assert example_for({"type": "string", "format": "email"}) == "qa@example.com"
    assert example_for({"anyOf": [{"type": "null"}, {"type": "boolean"}]}) is True
    assert len(example_for({"type": "string", "minLength": 20}, "n")) == 20
    assert wrong_type_for({"type": "integer"}) == "not-a-number"


def test_parse_json_variants():
    assert parse_json("```json\n[1, 2]\n```") == [1, 2]
    assert parse_json('Sure! {"a": 1} hope that helps') == {"a": 1}


class _ScriptedLLM:
    name = "scripted"

    def __init__(self, answer):
        self.answer = answer

    def complete(self, system, prompt, max_tokens=2000):
        if "Operation: POST /items" not in prompt:
            return "[]"
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _ai(operations, answer):
    gen = ScenarioGenerator(operations, _ScriptedLLM(answer))
    return [s for s in gen.generate() if s.source == "llm"], gen.llm_issues


def test_invented_rules_are_discarded(operations):
    import json

    hallucinated = json.dumps(
        [
            {"name": "Reject duplicate item name", "contract_basis": "item names must be unique", "expected_status": [422]},
            {"name": "Teapot", "contract_basis": "Create an inventory item", "expected_status": [418]},
        ]
    )
    scenarios, issues = _ai(operations, hallucinated)
    assert scenarios == []
    assert "discarded 2 ungrounded AI scenario(s)" in issues[0]


def test_wrapped_json_and_errors_are_handled(operations):
    import json

    wrapped = json.dumps({"scenarios": [{"name": "Name too long", "contract_basis": "maxLength: 100", "expected_status": [422]}]})
    scenarios, issues = _ai(operations, wrapped)
    assert len(scenarios) == 1 and issues == []

    scenarios, issues = _ai(operations, RuntimeError("Groq returned an empty answer"))
    assert scenarios == [] and "empty answer" in issues[0]
