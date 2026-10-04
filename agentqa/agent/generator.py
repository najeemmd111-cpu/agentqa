"""Scenario generation.

Two sources, merged and de-duplicated:
  1. Rules: systematic, schema-driven cases (happy path, missing / wrong-typed
     fields, min/max boundaries, enums, oversized strings, unknown ids, no auth).
  2. LLM: reads the operation contract + the rule-based plan and proposes what
     rules cannot know: business-rule and cross-field scenarios.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .llm import LLMClient, OfflineLLM, parse_json_list
from .models import Category, Operation, Scenario, Step
from .schema_synth import example_for, object_props, wrong_type_for

log = logging.getLogger(__name__)

NOT_FOUND_ID = 987654321
HUGE_STRING_LEN = 5000


class ScenarioGenerator:
    def __init__(self, operations: list[Operation], llm: LLMClient | None = None, max_llm_per_op: int = 6):
        self.ops = operations
        self.llm = llm or OfflineLLM()
        self.max_llm_per_op = max_llm_per_op
        self._counter: dict[str, int] = {}
        self.llm_issues: list[str] = []  # surfaced in the report so silent fallbacks are visible

    # ------------------------------------------------------------------ public
    def generate(self) -> list[Scenario]:
        scenarios: list[Scenario] = []
        rule_based_by_op: dict[str, list[Scenario]] = {}
        for op in self.ops:
            rule_based_by_op[op.operation_id] = self._rule_scenarios(op)
            scenarios += rule_based_by_op[op.operation_id]
        if isinstance(self.llm, OfflineLLM):
            return _dedupe(scenarios)
        # LLM calls are rate-limited and cost money: write operations first, and skip
        # operations with nothing to reason about (no body, no parameters, no auth).
        worth_asking = [op for op in self.ops if op.request_schema or op.parameters or op.requires_auth]
        worth_asking.sort(key=lambda op: op.method == "GET")
        for op in worth_asking:
            try:
                scenarios += self._llm_scenarios(op, rule_based_by_op[op.operation_id])
            except Exception as exc:  # LLM is an enhancement, never a single point of failure
                log.warning("LLM generation failed for %s: %s", op.operation_id, exc)
                self.llm_issues.append(f"Scenario generation for {op.label()} failed: {exc}")
        return _dedupe(scenarios)

    # ------------------------------------------------------------------ helpers
    def _id(self, op: Operation) -> str:
        n = self._counter.get(op.operation_id, 0) + 1
        self._counter[op.operation_id] = n
        return f"{op.operation_id}-{n:03d}"

    def _creator_for(self, collection_path: str) -> Operation | None:
        return next((o for o in self.ops if o.method == "POST" and o.path == collection_path), None)

    def _setup_and_vars(self, op: Operation) -> tuple[list[Step], dict[str, Any]]:
        """Create prerequisite resources for path params like /items/{item_id}."""
        steps: list[Step] = []
        path_values: dict[str, Any] = {}
        for p in op.path_params:
            name = p["name"]
            parent = op.path.split("/{" + name + "}")[0]
            creator = self._creator_for(parent)
            if creator and creator is not op:
                steps.append(Step(method="POST", path=creator.path, body=self._valid_body(creator, steps), capture={name: "id"}))
                path_values[name] = "{" + name + "}"
            else:
                path_values[name] = example_for(p.get("schema"), name)
        return steps, path_values

    def _valid_body(self, op: Operation, steps: list[Step] | None = None) -> Any:
        """Valid example body. Foreign keys like `item_id` get a setup step that creates the referenced resource."""
        if not op.request_schema:
            return None
        body = example_for(op.request_schema)
        if steps is not None and isinstance(body, dict):
            for key in list(body):
                if key.endswith("_id"):
                    creator = self._creator_for("/" + key[:-3] + "s")
                    if creator and creator is not op:
                        steps.append(Step(method="POST", path=creator.path, body=example_for(creator.request_schema), capture={key: "id"}))
                        body[key] = "{" + key + "}"
        return body

    def _valid_query(self, op: Operation) -> dict[str, Any]:
        return {p["name"]: example_for(p.get("schema"), p["name"]) for p in op.query_params if p.get("required")}

    @staticmethod
    def _codes(op: Operation, prefix: str, fallback: list[int]) -> list[int]:
        codes = [int(c) for c in op.responses if c.isdigit() and c.startswith(prefix)]
        return codes or fallback

    def _validation_codes(self, op: Operation) -> list[int]:
        codes = [int(c) for c in op.responses if c in ("400", "422")]
        return codes or [400, 422]

    # ------------------------------------------------------------------ rules
    def _rule_scenarios(self, op: Operation) -> list[Scenario]:
        out: list[Scenario] = []
        success = self._codes(op, "2", [200, 201, 204])
        invalid = self._validation_codes(op)

        def make(
            name: str,
            category: Category,
            *,
            body: Any = None,
            send_body: bool | None = None,
            query: dict | None = None,
            path_override: dict | None = None,
            use_auth: bool = True,
            expected: list[int] | None = None,
            forbid_5xx_only: bool = False,
            rationale: str = "",
            meta: dict | None = None,
        ) -> Scenario:
            steps, path_values = self._setup_and_vars(op)
            valid_body = self._valid_body(op, steps)  # also adds setup for foreign keys ({item_id})
            if body == "__valid__":
                body = valid_body
            return Scenario(
                id=self._id(op),
                operation_id=op.operation_id,
                name=name,
                category=category,
                method=op.method,
                path=op.path,
                path_params={**path_values, **(path_override or {})},
                query=query if query is not None else self._valid_query(op),
                body=body,
                send_body=op.request_schema is not None if send_body is None else send_body,
                use_auth=use_auth,
                setup=steps,
                expected_status=expected or [],
                forbid_5xx_only=forbid_5xx_only,
                rationale=rationale,
                meta=meta or {},
            )

        # 1. Happy path
        out.append(
            make(
                f"{op.label()} succeeds with valid input",
                Category.FUNCTIONAL,
                body="__valid__",
                expected=success,
                rationale="Baseline contract check",
            )
        )

        # 2. Body validation
        props, required = object_props(op.request_schema)
        if op.request_schema:
            out.append(
                make("Empty JSON body is rejected", Category.VALIDATION, body={}, expected=invalid, rationale="All required fields missing")
            )
        for field in required:

            def without(f=field):
                steps: list[Step] = []
                b = self._valid_body(op, steps)
                b.pop(f, None)
                return b

            out.append(
                make(
                    f"Missing required field '{field}' is rejected",
                    Category.VALIDATION,
                    body=without(),
                    expected=invalid,
                    rationale=f"'{field}' is required",
                )
            )
        for field, schema in props.items():

            def with_value(value, f=field):
                b = self._valid_body(op, [])
                b[f] = value
                return b

            out.append(
                make(
                    f"Wrong type for '{field}' is rejected", Category.VALIDATION, body=with_value(wrong_type_for(schema)), expected=invalid
                )
            )
            if "minimum" in schema:
                lo = schema["minimum"]
                step = 1 if schema.get("type") == "integer" else 0.01
                out.append(
                    make(
                        f"'{field}' below minimum ({lo - step}) is rejected",
                        Category.VALIDATION,
                        body=with_value(lo - step),
                        expected=invalid,
                        rationale=f"Contract: minimum {lo}",
                        meta={"field": field, "kind": "below_min", "limit": lo, "step": step},
                    )
                )
                out.append(make(f"'{field}' at minimum ({lo}) is accepted", Category.BOUNDARY, body=with_value(lo), expected=success))
            if "maximum" in schema:
                hi = schema["maximum"]
                step = 1 if schema.get("type") == "integer" else 0.01
                out.append(
                    make(
                        f"'{field}' above maximum ({hi + step}) is rejected",
                        Category.VALIDATION,
                        body=with_value(hi + step),
                        expected=invalid,
                        rationale=f"Contract: maximum {hi}",
                        meta={"field": field, "kind": "above_max", "limit": hi, "step": step},
                    )
                )
            if "maxLength" in schema:
                n = schema["maxLength"]
                out.append(
                    make(
                        f"'{field}' longer than maxLength ({n + 1} chars) is rejected",
                        Category.VALIDATION,
                        body=with_value("x" * (n + 1)),
                        expected=invalid,
                        rationale=f"Contract: maxLength {n}",
                        meta={"field": field, "kind": "too_long", "limit": n},
                    )
                )
                out.append(
                    make(f"'{field}' at maxLength ({n} chars) is accepted", Category.BOUNDARY, body=with_value("x" * n), expected=success)
                )
            if "enum" in schema:
                out.append(
                    make(
                        f"Unknown enum value for '{field}' is rejected",
                        Category.VALIDATION,
                        body=with_value("__not_in_enum__"),
                        expected=invalid,
                    )
                )
            if schema.get("type") == "string" or "maxLength" in schema:
                out.append(
                    make(
                        f"Oversized '{field}' ({HUGE_STRING_LEN} chars) never causes a server error",
                        Category.ROBUSTNESS,
                        body=with_value("A" * HUGE_STRING_LEN),
                        forbid_5xx_only=True,
                        rationale="Any 4xx is fine; a 5xx is a crash",
                        meta={"field": field, "kind": "huge_string", "low": schema.get("maxLength", 1)},
                    )
                )

        # 3. Query parameter validation
        for p in op.query_params:
            schema = p.get("schema") or {}
            if "minimum" in schema:
                out.append(
                    make(
                        f"Query '{p['name']}' below minimum is rejected",
                        Category.VALIDATION,
                        body="__valid__",
                        query={**self._valid_query(op), p["name"]: schema["minimum"] - 1},
                        expected=invalid,
                    )
                )
            if "maximum" in schema:
                out.append(
                    make(
                        f"Query '{p['name']}' above maximum is rejected",
                        Category.VALIDATION,
                        body="__valid__",
                        query={**self._valid_query(op), p["name"]: schema["maximum"] + 1},
                        expected=invalid,
                    )
                )

        # 4. Unknown resource
        if op.path_params and "404" in op.responses:
            override = {p["name"]: NOT_FOUND_ID for p in op.path_params}
            out.append(
                make(
                    f"{op.label()} with unknown id returns 404",
                    Category.NOT_FOUND,
                    body="__valid__",
                    path_override=override,
                    expected=[404],
                    meta={"kind": "unknown_id"},
                )
            )

        # 5. Authentication
        if op.requires_auth:
            out.append(
                make(
                    f"{op.label()} without credentials is refused",
                    Category.SECURITY,
                    body="__valid__",
                    use_auth=False,
                    expected=[401, 403],
                    rationale="Operation declares a security requirement",
                    meta={"kind": "no_auth"},
                )
            )
        return out

    # ------------------------------------------------------------------ LLM
    @staticmethod
    def _contract_text(op: Operation) -> str:
        parts = [op.summary, json.dumps(op.request_schema or {}), json.dumps(op.parameters)]
        parts += [f"{code} {desc}" for code, desc in op.responses.items()]
        return " ".join(parts)

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(str(text).lower().replace('"', "").replace("'", "").split())

    def _llm_scenarios(self, op: Operation, rule_based: list[Scenario]) -> list[Scenario]:
        steps, path_values = self._setup_and_vars(op)
        valid = self._valid_body(op, steps)
        variables = list(path_values) + [k for st in steps for k in st.capture]
        system = (
            "You are a senior QA engineer. You design API test scenarios that systematic schema-based rules miss: "
            "business rules and state that the contract states in words (summary, response descriptions). "
            "You NEVER invent requirements: every scenario must be justified by a literal quote from the contract. "
            "Answer with a JSON array only."
        )
        prompt = f"""CONTRACT
Operation: {op.label()}
Summary: {op.summary}
Requires auth: {op.requires_auth}
Request body schema: {json.dumps(op.request_schema)}
Documented responses: {json.dumps(op.responses)}

TEST HARNESS
Before each scenario these setup requests run (note the bodies: they define the starting state, e.g. stock levels):
{json.dumps([st.model_dump() for st in steps])}
Captured variables you can use as string placeholders in body/path_params values: {json.dumps(["{" + v + "}" for v in variables])}
A valid request body is: {json.dumps(valid)}

Already covered (do NOT repeat): {json.dumps([sc.name for sc in rule_based])}

TASK
Propose up to {self.max_llm_per_op} NEW scenarios that check rules stated in the CONTRACT text above.
If the contract states no extra rule, return [].
Each item:
{{"name": str, "category": "business"|"security"|"validation"|"boundary"|"functional",
  "contract_basis": "<exact quote copied from the CONTRACT that requires this behaviour>",
  "body": object|null, "query": object, "path_params": object, "use_auth": bool,
  "expected_status": [int], "rationale": str}}
expected_status must only use documented response codes."""
        raw = parse_json_list(self.llm.complete(system, prompt, max_tokens=4000))
        contract = self._norm(self._contract_text(op))
        documented = {int(c) for c in op.responses if c.isdigit()}
        out: list[Scenario] = []
        rejected: list[str] = []
        for item in raw[: self.max_llm_per_op]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "LLM scenario"))[:160]
            basis = self._norm(item.get("contract_basis", ""))
            expected = [int(c) for c in item.get("expected_status", []) if str(c).isdigit()]
            # Grounding checks: reject anything the contract does not actually say.
            if len(basis) < 8 or basis not in contract:
                rejected.append(f"'{name}' (no verbatim contract quote)")
                continue
            if not expected or not set(expected) <= documented:
                rejected.append(f"'{name}' (expects undocumented status {expected})")
                continue
            path_params = dict(path_values)
            for k, v in (item.get("path_params") or {}).items():
                if k in path_values and (isinstance(v, int) or (isinstance(v, str) and (v.isdigit() or v in ("{" + k + "}",)))):
                    path_params[k] = v
            try:
                category = Category(item.get("category", "business"))
            except ValueError:
                category = Category.BUSINESS
            out.append(
                Scenario(
                    id=self._id(op),
                    operation_id=op.operation_id,
                    name=name,
                    category=category,
                    method=op.method,
                    path=op.path,
                    path_params=path_params,
                    query=item.get("query") or {},
                    body=item.get("body"),
                    send_body=op.request_schema is not None,
                    use_auth=bool(item.get("use_auth", True)),
                    setup=steps,
                    expected_status=expected,
                    rationale=f'{item.get("rationale", "")} [contract: "{item.get("contract_basis", "")}"]'.strip(),
                    source="llm",
                )
            )
        if rejected:
            self.llm_issues.append(f"{op.label()}: discarded {len(rejected)} ungrounded AI scenario(s): " + "; ".join(rejected))
        return out


def _dedupe(scenarios: list[Scenario]) -> list[Scenario]:
    seen: set[str] = set()
    out: list[Scenario] = []
    for s in scenarios:
        key = json.dumps([s.method, s.path, s.path_params, s.query, s.body, s.use_auth, s.forbid_5xx_only], sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
