---
title: LLM worker endpoints — structured JSON + code-owned guardrail backstop
tags: [gemini, structured-json, worker, prompt-injection, interview, llm-endpoint, cost-tracking]
problem_type: pattern
symptoms: new worker endpoint that takes user input into an LLM; needs to never 5xx, never let the model mint/escape, and track cost
root_cause: n/a (pattern)
date: 2026-07-03
---

Pattern established building the interview engine (slice #1, `agents/interview/`, route in
`agents/worker/interview_routes.py`). Reuse it for any worker endpoint that feeds untrusted
user input into Gemini.

**1. Structured JSON via the shared client.** `LLMClient.call_gemini_json(prompt,
response_schema, ...)` in `agents/pipeline/llm_clients.py` sets
`response_mime_type="application/json"` + `response_schema` (a pydantic model) and returns a
`GeminiJsonResult(parsed, prompt_tokens, output_tokens, total_tokens, elapsed_ms, model)`.
Token counts come from `response.usage_metadata` (coerce None→0 via `_usage_int`) — that's
your per-turn cost/latency for logging. `response.parsed is None` raises so the caller maps
it to a graceful retry, not a silent None. Mock THIS method (the boundary) in tests — no live
Gemini.

**2. The model does wording; code owns every hard rule (CLAUDE.md Rule 5).** Do not trust
model output for anything enforceable. In the interview: server computes termination
(drill/tap caps), derives the ladder from the slug, validates root-anchoring + traceability +
dedup, caps bubble counts, and always appends fixed affordances. The model can propose but
never mint — a prompt-injected/misbehaving model still can't produce an out-of-taxonomy or
untraced result. When you widen a traceability rule (e.g. "user typed free text"), scope the
widening tightly (root-level parks only) or a reviewer will (correctly) call it a bypass.

**3. Never 5xx.** The engine function never raises; it returns a typed
`{response_kind: "retry", ...}` body and the route returns HTTP 200. Mirrors the Q&A route's
graceful-failure contract (`agents/worker/main.py`).

**4. Auth + mounting.** Reuse `verify_supabase_user` (JWT, same seam as
`/feed/assemble-mine`) and apply it router-wide: `APIRouter(dependencies=[Depends(...)])` so
no future route is unauthenticated by omission. Add the path prefix to
`_RATE_LIMITED_PREFIXES` in `main.py` — it's a paid endpoint. Note: `verify_supabase_user`
logs `assemble_mine_*` event names; if that observability coupling matters, factor it into a
shared auth helper first.

**5. Bound request fields.** A `max_length` on the list is not enough — the whole transcript
is serialized into the prompt, so bound each string too (`free_text`, per-item bubble labels
via `Annotated[str, Field(max_length=...)]`) or you have a token-cost DoS vector.

**6. Agent files stay < 500 lines** (CLAUDE.md). Split pure guardrails (`guards.py`) from the
orchestrator (`engine.py`) — the split also makes the guardrails trivially unit-testable.
