"""Load an OpenAPI 3 document (YAML/JSON, file/URL/dict) into Operation objects."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from .models import Operation

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def load_spec(source: str | Path | dict) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    text: str
    s = str(source)
    if s.startswith(("http://", "https://")):
        resp = httpx.get(s, timeout=15)
        resp.raise_for_status()
        text = resp.text
    else:
        text = Path(s).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def resolve_refs(node: Any, root: dict[str, Any], _depth: int = 0) -> Any:
    """Inline local $refs (and merge allOf) so downstream code sees plain schemas."""
    if _depth > 25:
        return {}
    if isinstance(node, list):
        return [resolve_refs(n, root, _depth + 1) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part]
        return resolve_refs(copy.deepcopy(target), root, _depth + 1)
    out = {k: resolve_refs(v, root, _depth + 1) for k, v in node.items()}
    if "allOf" in out:
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for part in out.pop("allOf"):
            merged["properties"].update(part.get("properties", {}))
            merged["required"] += part.get("required", [])
        merged["properties"].update(out.pop("properties", {}))
        merged["required"] += out.pop("required", [])
        out = {**merged, **out}
    return out


def parse_operations(spec: dict[str, Any]) -> list[Operation]:
    global_security = bool(spec.get("security"))
    ops: list[Operation] = []
    for path, item in (spec.get("paths") or {}).items():
        shared_params = item.get("parameters", [])
        for method in HTTP_METHODS:
            op = item.get(method)
            if not op:
                continue
            params = resolve_refs(shared_params + op.get("parameters", []), spec)
            body_schema = None
            content = (op.get("requestBody") or {}).get("content", {})
            if "application/json" in content:
                body_schema = resolve_refs(content["application/json"].get("schema", {}), spec)
            security = op.get("security")
            requires_auth = bool(security) if security is not None else global_security
            ops.append(
                Operation(
                    operation_id=op.get("operationId") or f"{method}_{path}".replace("/", "_").strip("_"),
                    method=method.upper(),
                    path=path,
                    summary=op.get("summary") or op.get("description") or "",
                    parameters=params,
                    request_schema=body_schema,
                    responses={str(k): (v or {}).get("description", "") for k, v in (op.get("responses") or {}).items()},
                    requires_auth=requires_auth,
                )
            )
    return ops
