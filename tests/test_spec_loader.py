from agentqa.agent.spec_loader import parse_operations, resolve_refs


def test_parses_all_operations(operations):
    labels = {op.label() for op in operations}
    assert labels == {"GET /health", "GET /items", "POST /items", "GET /items/{item_id}", "DELETE /items/{item_id}", "POST /orders"}


def test_security_and_path_params(operations):
    by_id = {op.operation_id: op for op in operations}
    assert by_id["deleteItem"].requires_auth
    assert not by_id["getItem"].requires_auth
    assert [p["name"] for p in by_id["getItem"].path_params] == ["item_id"]  # path-level params inherited


def test_refs_are_inlined(operations):
    create = next(op for op in operations if op.operation_id == "createItem")
    props = create.request_schema["properties"]
    assert props["name"]["maxLength"] == 100
    assert create.request_schema["required"] == ["name", "price", "quantity"]


def test_allof_is_merged():
    spec = {"components": {"schemas": {"A": {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}}}}
    merged = resolve_refs({"allOf": [{"$ref": "#/components/schemas/A"}, {"properties": {"b": {"type": "integer"}}}]}, spec)
    assert set(merged["properties"]) == {"a", "b"}
    assert merged["required"] == ["a"]


def test_global_security_applies():
    spec = {
        "security": [{"key": []}],
        "paths": {"/x": {"get": {"responses": {"200": {}}}}, "/open": {"get": {"security": [], "responses": {"200": {}}}}},
    }
    ops = {o.path: o for o in parse_operations(spec)}
    assert ops["/x"].requires_auth and not ops["/open"].requires_auth
