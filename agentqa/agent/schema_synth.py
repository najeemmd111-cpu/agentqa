"""Synthesise valid / invalid values from JSON Schema fragments."""

from __future__ import annotations

from typing import Any


def _pick_variant(schema: dict[str, Any]) -> dict[str, Any]:
    for key in ("anyOf", "oneOf"):
        if key in schema:
            for v in schema[key]:
                if v.get("type") != "null":
                    return v
    return schema


def example_for(schema: dict[str, Any] | None, name: str = "") -> Any:
    """A value that satisfies the schema (best effort)."""
    if not schema:
        return "sample"
    schema = _pick_variant(schema)
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        return {k: example_for(v, k) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [example_for(schema.get("items", {}), name)]
    if t == "integer":
        lo = schema.get("minimum", 1)
        hi = schema.get("maximum", max(lo, 1) + 10)
        return int(min(max(lo, 1), hi))
    if t == "number":
        lo = schema.get("minimum", 1.0)
        return float(max(lo, 1.5))
    if t == "boolean":
        return True
    fmt = schema.get("format")
    if fmt == "email":
        return "qa@example.com"
    if fmt == "date":
        return "2026-01-15"
    if fmt == "date-time":
        return "2026-01-15T10:00:00Z"
    if fmt == "uuid":
        return "123e4567-e89b-12d3-a456-426614174000"
    base = f"test-{name}" if name else "test"
    min_len = schema.get("minLength", 0)
    max_len = schema.get("maxLength")
    value = base if len(base) >= min_len else base + "x" * (min_len - len(base))
    return value[:max_len] if max_len else value


def wrong_type_for(schema: dict[str, Any]) -> Any:
    t = _pick_variant(schema).get("type")
    if t in ("integer", "number"):
        return "not-a-number"
    if t == "boolean":
        return "not-a-bool"
    if t == "array":
        return {"unexpected": "object"}
    if t == "object":
        return "not-an-object"
    return 12345  # strings get a number


def object_props(schema: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    if not schema:
        return {}, []
    schema = _pick_variant(schema)
    return schema.get("properties") or {}, list(schema.get("required") or [])
