"""A small inventory API with deliberately planted bugs.

Contract: demo_target/openapi.yaml. Planted bugs (do not fix — AgentQA should find them):

  BUG-1  POST /items accepts a negative `quantity` (contract: minimum 0).
  BUG-2  POST /items crashes with 500 when `name` is longer than 120 chars
         (simulated DB column overflow; contract says 422 above 100).
  BUG-3  GET /items/{id} returns 200 + null for unknown ids (contract: 404).
  BUG-4  DELETE /items/{id} does not check the bearer token (contract: 401).
  BUG-5  POST /orders lets you order more than the available stock (contract: 409).
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

TOKEN = "demo-token"

app = FastAPI(title="Inventory API (buggy demo)")

_items: dict[int, dict] = {}
_orders: dict[int, dict] = {}
_seq = {"item": 0, "order": 0}


class ItemCreate(BaseModel):
    name: str = Field(min_length=1)  # BUG-2: no max_length
    price: float = Field(ge=0)
    quantity: int = Field(le=10000)  # BUG-1: no ge=0
    category: str | None = None


class OrderCreate(BaseModel):
    item_id: int
    quantity: int = Field(ge=1)


def _require_token(authorization: str | None) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="Missing or invalid token")


def reset() -> None:
    _items.clear()
    _orders.clear()
    _seq.update(item=0, order=0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/items")
def list_items(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = sorted(_items.values(), key=lambda i: i["id"])
    return items[offset : offset + limit]


@app.post("/items", status_code=201)
def create_item(body: ItemCreate, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    if len(body.name) > 120:  # BUG-2: storage overflow surfaces as a 500
        raise RuntimeError("value too long for column name VARCHAR(120)")
    if body.category is not None and body.category not in {"hardware", "software", "accessory"}:
        raise HTTPException(status_code=422, detail="invalid category")
    _seq["item"] += 1
    item = {"id": _seq["item"], **body.model_dump()}
    _items[item["id"]] = item
    return item


@app.get("/items/{item_id}")
def get_item(item_id: int):
    return _items.get(item_id)  # BUG-3: should 404 when missing


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, authorization: str | None = Header(default=None)):
    # BUG-4: token never checked
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    del _items[item_id]
    return Response(status_code=204)


@app.post("/orders", status_code=201)
def create_order(body: OrderCreate, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    item = _items.get(body.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown item")
    # BUG-5: missing stock check -> stock can go negative
    item["quantity"] -= body.quantity
    _seq["order"] += 1
    order = {"id": _seq["order"], "item_id": item["id"], "quantity": body.quantity, "total": round(item["price"] * body.quantity, 2)}
    _orders[order["id"]] = order
    return order
