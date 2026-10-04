# AgentQA report — Inventory API (demo target)

*Run `36d185ab` · LLM: `offline` · 1.4s*

**34/39 scenarios passed · 5 failed · 4 findings**

## Summary

5 of 39 scenarios failed, grouped into 4 findings (1 critical, 1 high, 2 medium). Fix first — Endpoint does not enforce authentication: DELETE /items/{item_id}. The handler never validates the Authorization header. Then: Server crashes (5xx) on unexpected input: POST /items; Unknown resources do not return 404: GET /items/{item_id}; Invalid input is accepted: POST /items.

## Findings

### 1. 🔴 Endpoint does not enforce authentication: DELETE /items/{item_id}
- **Severity:** critical · **Risk area:** authentication & authorization
- **Likely cause:** The handler never validates the Authorization header.
- **Suggested fix:** Add the same auth dependency/guard used by the other protected endpoints and a regression test.
- **Evidence:**
  - deleteItem-003: DELETE /items/{item_id} without credentials is refused → expected [401, 403], got 204
  - DELETE /items/{item_id}: accepts requests without credentials (status 204); with a wrong token it returned 204 — the token is not checked at all.

### 2. 🟠 Server crashes (5xx) on unexpected input: POST /items
- **Severity:** high · **Risk area:** robustness & stability
- **Likely cause:** An unhandled exception escapes the handler, typically a storage constraint hit before validation.
- **Suggested fix:** Validate the input at the API boundary (e.g. max length) and map storage errors to 4xx.
- **Evidence:**
  - createItem-009: Oversized 'name' (5000 chars) never causes a server error → server error 500
  - POST /items: 'name' crashes the server from 121 characters (≤120 is handled) — found by bisection in 12 probes. Looks like an unvalidated column-size limit.

### 3. 🟡 Unknown resources do not return 404: GET /items/{item_id}
- **Severity:** medium · **Risk area:** core functionality
- **Likely cause:** The handler returns the lookup result even when it is empty.
- **Suggested fix:** Raise a 404 when the resource does not exist.
- **Evidence:**
  - getItem-002: GET /items/{item_id} with unknown id returns 404 → expected [404], got 200
  - GET /items/{item_id}: unknown ids return 200 instead of 404 (confirmed with a second id → 200); body was 'null'.

### 4. 🟡 Invalid input is accepted: POST /items
- **Severity:** medium · **Risk area:** input validation
- **Likely cause:** Validation constraints from the contract are missing in the request model.
- **Suggested fix:** Add the missing constraints (minimum / maximum / maxLength / enum) to the request schema.
- **Evidence:**
  - createItem-014: 'quantity' below minimum (-1) is rejected → expected [422], got 201
  - createItem-007: 'name' longer than maxLength (101 chars) is rejected → expected [422], got 201
  - POST /items: 'quantity' minimum 0 is not enforced — every probed value was accepted (-100, -1000000).
  - POST /items: 'name' maxLength 100 is not validated (server accepted 101 chars).

## Agent investigation log

- DELETE /items/{item_id}: accepts requests without credentials (status 204); with a wrong token it returned 204 — the token is not checked at all.
- POST /items: 'name' crashes the server from 121 characters (≤120 is handled) — found by bisection in 12 probes. Looks like an unvalidated column-size limit.
- POST /items: 'quantity' minimum 0 is not enforced — every probed value was accepted (-100, -1000000).
- POST /items: 'name' maxLength 100 is not validated (server accepted 101 chars).
- GET /items/{item_id}: unknown ids return 200 instead of 404 (confirmed with a second id → 200); body was 'null'.

## Coverage by risk area

| Risk area | Passed | Failed |
|---|---|---|
| authentication & authorization | 2 | 1 |
| core functionality | 8 | 1 |
| input validation | 23 | 2 |
| robustness & stability | 1 | 1 |

## All scenarios (highest risk first)

| | ID | Scenario | Category | Risk | Status | Expected |
|---|---|---|---|---|---|---|
| ❌ | `deleteItem-003` | DELETE /items/{item_id} without credentials is refused | security | 0.80 | 204 | 401,403 |
| ✅ | `createItem-020` | POST /items without credentials is refused | security | 0.72 | 401 | 401,403 |
| ✅ | `createOrder-009` | POST /orders without credentials is refused | security | 0.72 | 401 | 401,403 |
| ❌ | `createItem-009` | Oversized 'name' (5000 chars) never causes a server error | robustness | 0.60 | 500 | not 5xx |
| ✅ | `createItem-019` | Oversized 'category' (5000 chars) never causes a server error | robustness | 0.60 | 422 | not 5xx |
| ✅ | `deleteItem-001` | DELETE /items/{item_id} succeeds with valid input | functional | 0.56 | 204 | 204 |
| ✅ | `deleteItem-002` | DELETE /items/{item_id} with unknown id returns 404 | not_found | 0.55 | 404 | 404 |
| ✅ | `createItem-001` | POST /items succeeds with valid input | functional | 0.51 | 201 | 201 |
| ✅ | `createOrder-001` | POST /orders succeeds with valid input | functional | 0.49 | 201 | 201 |
| ✅ | `createItem-008` | 'name' at maxLength (100 chars) is accepted | boundary | 0.48 | 201 | 201 |
| ✅ | `createItem-004` | Missing required field 'price' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-002` | Empty JSON body is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createOrder-002` | Empty JSON body is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-003` | Missing required field 'name' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-005` | Missing required field 'quantity' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createOrder-004` | Missing required field 'quantity' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-018` | Unknown enum value for 'category' is rejected | validation | 0.46 | 422 | 422 |
| ❌ | `createItem-014` | 'quantity' below minimum (-1) is rejected | validation | 0.46 | 201 | 422 |
| ✅ | `createOrder-007` | 'quantity' below minimum (0) is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-006` | Wrong type for 'name' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-010` | Wrong type for 'price' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createOrder-003` | Missing required field 'item_id' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-013` | Wrong type for 'quantity' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createItem-017` | Wrong type for 'category' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createOrder-006` | Wrong type for 'quantity' is rejected | validation | 0.46 | 422 | 422 |
| ✅ | `createOrder-005` | Wrong type for 'item_id' is rejected | validation | 0.45 | 422 | 422 |
| ✅ | `createItem-016` | 'quantity' above maximum (10001) is rejected | validation | 0.45 | 422 | 422 |
| ✅ | `createItem-011` | 'price' below minimum (-0.01) is rejected | validation | 0.45 | 422 | 422 |
| ❌ | `createItem-007` | 'name' longer than maxLength (101 chars) is rejected | validation | 0.43 | 201 | 422 |
| ✅ | `createOrder-008` | 'quantity' at minimum (1) is accepted | boundary | 0.43 | 201 | 201 |
| ✅ | `createItem-012` | 'price' at minimum (0) is accepted | boundary | 0.42 | 201 | 201 |
| ✅ | `createItem-015` | 'quantity' at minimum (0) is accepted | boundary | 0.42 | 201 | 201 |
| ✅ | `listItems-001` | GET /items succeeds with valid input | functional | 0.35 | 200 | 200 |
| ✅ | `getItem-001` | GET /items/{item_id} succeeds with valid input | functional | 0.34 | 200 | 200 |
| ❌ | `getItem-002` | GET /items/{item_id} with unknown id returns 404 | not_found | 0.34 | 200 | 404 |
| ✅ | `health-001` | GET /health succeeds with valid input | functional | 0.34 | 200 | 200 |
| ✅ | `listItems-004` | Query 'offset' below minimum is rejected | validation | 0.30 | 422 | 422 |
| ✅ | `listItems-003` | Query 'limit' above maximum is rejected | validation | 0.30 | 422 | 422 |
| ✅ | `listItems-002` | Query 'limit' below minimum is rejected | validation | 0.30 | 422 | 422 |
