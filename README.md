# AgentQA

An LLM agent that takes an API spec, **autonomously generates test scenarios (functional + edge cases), executes them, investigates the failures with follow-up probes, and explains the bugs in plain language.**

**Stack:** Python 3.11+ · FastAPI · Anthropic SDK (Claude) or Groq · pytest · embeddings for risk-based prioritisation · Docker · GitHub Actions.

```
 OpenAPI spec ─▶ ① PLAN ──────────▶ ② PRIORITISE ─────▶ ③ ACT ─────────▶ ④ INVESTIGATE ─────▶ ⑤ EXPLAIN
                 rules + LLM          embeddings →         httpx or          agent probes each      findings +
                 scenarios            risk area + score    generated pytest  failure (bisection,    plain-language
                                                           suite             confirmation, LLM)     summary + report
```

## What it actually finds

The repo ships a small inventory API (`demo_target/`) with **5 planted bugs** and the contract it should honour (`demo_target/openapi.yaml`). One offline run (no API key) produces [`examples/sample-report.md`](examples/sample-report.md):

| Planted bug | Found by | How |
|---|---|---|
| `DELETE /items/{id}` doesn't check the token | rules → **critical** | security scenario; the agent then retries with a *wrong* token and concludes the token is never checked |
| `POST /items` crashes (500) on long names | rules → **high** | robustness scenario; the agent **bisects** the payload size and reports the exact threshold: *crashes from 121 characters* |
| `GET /items/{id}` returns 200 + `null` for unknown ids | rules → medium | not-found scenario, confirmed with a second id |
| `quantity` minimum 0 not enforced | rules → medium | boundary scenario, confirmed with −100 and −1,000,000 |
| Ordering more than the stock is accepted | **LLM only** | business-rule scenario: the LLM reads "must not exceed available stock", sees the setup item has 25 units, and orders 26 expecting 409 |

That last row is the point of the LLM: systematic rules cover the contract's syntax, the LLM covers its *meaning*. To stop the LLM from inventing requirements, every AI scenario must quote the contract text it tests (`contract_basis`) and may only expect documented status codes; anything else is discarded and listed under "AI issues" in the report. AI-proposed scenarios are marked 🤖. Everything degrades gracefully. Without a key, or if the LLM returns garbage, the rule-based pipeline still runs.

## Quick start

```bash
pip install -e ".[dev]"

# terminal 1: the buggy target
uvicorn demo_target.app:app --port 9000

# terminal 2: the agent
export ANTHROPIC_API_KEY=...        # or GROQ_API_KEY=..., or nothing for offline mode
agentqa run --spec demo_target/openapi.yaml --base-url http://localhost:9000 \
            --auth "Bearer demo-token" --out agentqa-report
```

Output: `agentqa-report/report.md` (human-readable) and `report.json` (machine-readable).

With Docker: `docker compose up --build`, then `POST http://localhost:8000/runs` with `{"spec": "demo_target/openapi.yaml", "base_url": "http://demo:9000", "auth_header": "Bearer demo-token"}`.

### CLI

| Command | Purpose |
|---|---|
| `agentqa run --spec S --base-url U [--auth H] [--llm anthropic\|groq\|offline] [--engine httpx\|pytest]` | Full agent loop + report |
| `… --fail-on critical\|high\|medium\|any` | Exit code 1 if findings at that severity exist: use as a **CI quality gate** (see `examples/agentqa-gate.yml`) |
| `agentqa generate --spec S --base-url U --out generated_tests` | Only generate, and export a standalone **pytest suite** you can commit |

### REST API (FastAPI, docs at `/docs`)

| Method | Path | |
|---|---|---|
| POST | `/runs` | start an agent run (async, returns `run_id`) |
| GET | `/runs/{id}` | status + full JSON report |
| GET | `/runs/{id}/report.md` | Markdown report |
| POST | `/classify` | document attribute classification (see below) |
| POST | `/extract` | entity extraction (see below) |

## How each stage works

**① Plan: `agent/generator.py`.** Parses the OpenAPI document (resolving `$ref`/`allOf`) and, per operation, generates: happy path; empty body; each required field missing; wrong type per field; below-minimum / above-maximum; at-minimum and at-maxLength (must be *accepted*); over-maxLength; invalid enum; 5,000-char strings (must never 5xx); query-param bounds; unknown ids → 404; no credentials → 401/403. It builds **setup steps** automatically: `/items/{item_id}` first creates an item via `POST /items` and captures its id; a body field `item_id` creates the referenced item. Then the LLM gets the contract, the setup context and the list of already-covered cases, and proposes *new* business-rule / state / cross-field scenarios as JSON.

**② Prioritise: `agent/risk.py`, `agent/embeddings.py`.** Each scenario is embedded and assigned to the nearest risk area (auth, data integrity, robustness, core functionality, input validation) by cosine similarity to labelled prototype descriptions. Score = area weight × HTTP-method weight × similarity. Execution and the report are ordered by risk. The default embedder is a dependency-free hashing embedder (word + char n-grams); set `AGENTQA_EMBEDDINGS=st` after `pip install ".[embeddings]"` to use sentence-transformers.

**③ Act: `agent/executor.py`, `agent/runtime.py`.** Two engines share one runtime: `httpx` (in-process, fast) and `pytest`, which writes `test_agentqa_generated.py` + its JSON scenarios, runs pytest (JUnit XML output for CI), and reads detailed results back.

**④ Investigate: `agent/investigator.py`.** The agentic loop. For each failure the agent decides which follow-up requests to send and runs them for real. Deterministic strategies run first because they are precise: bisect crash thresholds, confirm validation gaps with extreme values, retry with a wrong token, retry with another unknown id, distinguish flaky from consistent 5xx. Failures none of them covers (typically business rules) go to the LLM, which proposes probes and then states what they prove.

**⑤ Explain: `agent/summarizer.py`, `agent/report.py`.** Failures are classified (missing auth, server error, business rule, accepted-invalid, wrong 404, …), grouped per endpoint into findings with severity, likely cause, suggested fix and evidence (including the investigation notes), then summarised in plain language by the LLM (template fallback offline).

## Reusable NLP module: `agentqa/nlp.py`

The same embedding engine powers a general document **attribute classifier** with presets for **confidentiality** (public / internal / confidential / secret), **jurisdiction** (France / EU / US / UK) and **document type**, bilingual FR/EN prototypes, custom label sets, and an optional LLM zero-shot mode. `extract_entities` pulls dates (FR/EN), amounts, percentages, emails, legal references (`article L. 1234-5`, U.S.C.) and ticket ids, plus LLM extraction of arbitrary named fields.

```bash
curl -X POST localhost:8000/classify -H 'content-type: application/json' \
  -d '{"texts": ["Arrêt de la Cour de cassation, article L. 1234-5 du Code du travail"], "preset": "jurisdiction"}'
# → [{"label": "France", "confidence": 0.332, "method": "embedding", ...}]
```

## Tests & CI

`pytest` runs 38 tests: spec parsing, generation (including a deterministic **fake LLM** to test the LLM paths), schema synthesis, risk ranking, embeddings, the full agent against the demo in-process, crash-threshold bisection, the **pytest engine vs httpx engine parity** against a live uvicorn server, the REST API lifecycle, the CLI and its CI gate, and the NLP module.

GitHub Actions (`.github/workflows/ci.yml`): ruff lint + format check → tests → **agent self-test** (runs AgentQA on the demo and posts the report to the job summary) → Docker build.

## Configuration

| Env var | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` / `GROQ_API_KEY` | – | enables that LLM (auto-detected) |
| `AGENTQA_LLM` | `auto` | `anthropic` \| `groq` \| `offline` |
| `AGENTQA_ANTHROPIC_MODEL` | `claude-sonnet-5-5` | any Claude model id |
| `AGENTQA_GROQ_MODEL` | `openai/gpt-oss-120b` | any Groq chat model |
| `AGENTQA_EMBEDDINGS` | `hashing` | `st` for sentence-transformers |

## Layout

```
agentqa/
  agent/
    spec_loader.py    OpenAPI → Operation models
    schema_synth.py   valid / invalid values from JSON Schema
    generator.py      rule + LLM scenario generation, setup chaining
    embeddings.py     hashing / sentence-transformer embedders
    risk.py           risk-area classification & scoring
    runtime.py        executes one scenario (shared by both engines)
    executor.py       httpx engine, pytest engine + exporter
    investigator.py   autonomous follow-up probing
    summarizer.py     findings + plain-language summary
    report.py         Markdown report
    orchestrator.py   the agent loop
    llm.py            Anthropic SDK, Groq, offline; robust JSON parsing
  nlp.py              document classification & entity extraction
  api.py              FastAPI service
  cli.py              CLI (run / generate, CI gate)
demo_target/          buggy inventory API + its contract
tests/                pytest suite
examples/             sample report, CI gate workflow for other repos
```

## Limitations / next steps

- JSON APIs with OpenAPI 3 only; no GraphQL or multipart uploads yet.
- Auth is a single static header; OAuth flows would need a token-fetch step.
- Stateful sequences beyond create-then-use (e.g. full order lifecycle) are left to the LLM.
- Next: UI-level testing via Playwright, and learning severity weights from triaged history.
