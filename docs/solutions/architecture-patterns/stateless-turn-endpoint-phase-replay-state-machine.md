---
title: Stateless turn endpoint — deterministic phase-replay state machine (LLM only at the edges)
tags: [interview, state-machine, stateless, replay, gemini, turn-protocol, multi-select, deferred]
problem_type: pattern
symptoms: a multi-phase, server-driven conversational flow behind a stateless one-turn-per-request endpoint that must be deterministic, bounded, and never dead-end
root_cause: n/a (pattern)
date: 2026-07-04
---

Established extending the interview engine for the multi-select INTERESTS phase (slice #14,
`agents/interview/phases.py` + `engine.py`). Reuse for any server-driven multi-phase flow
where the client is a dumb renderer and the whole conversation travels with each request.

**1. Replay the transcript into "what turn comes next", don't store sessions.** The endpoint
is stateless: `plan_interest_turn(conversation_state)` forward-simulates the deterministic
plan from turn 1 and returns the next phase + accumulated state (selections, deferred skips).
Every request re-derives everything; there is no server session to drift. The pending
(unanswered) turn is the ONLY one that triggers an LLM call — already-answered turns are just
consumed during replay, so at most one Gemini call per request however deep the flow.

**2. The machine owns the flow; the LLM only words the open turns (Rule 5).** Split turns into
*deterministic* (fixed roots, skip-fast-forward offers, open WHO drills — engine-worded, no
LLM, no retry risk) and *LLM* (sub-niche chip generation, terminal extraction). A phase enum
+ a small forward loop drives ordering; the model never decides when to advance or terminate.
This retires ad-hoc "let the model choose action" logic (we deleted a whole drill-per-root
`DrillState`) and makes the flow unit-testable with zero mocks — drive the machine with real
taps and assert the phase sequence.

**3. Detect control choices by matching fixed label constants echoed back — accept the tiny
coupling.** With no session, "did the user tap *skip this category* or *show options*?" is
answered by string-matching `CATEGORY_SKIP_ACCEPT_LABEL` (a constant) against the labels the
client echoes in `bubbles_tapped`. Render and match both run server-side in the same request
cycle, so the only drift window is a redeploy that RENAMES a constant between a client render
and its echo — acceptable for a sessionless contract, but document it at the match site so a
reviewer doesn't (correctly) call it fragile.

**4. Cap-and-fold any "one X per selection" rule so multi-select can't explode.** "Exactly one
WHO drill per selected sub-niche" becomes 40 questions if a user taps everything. Compress:
the first `CAP - 1` get individual turns, the rest fold into ONE combined turn — total never
exceeds `CAP`. Return a discriminated union of dataclasses (`WhoDrill | CombinedWhoDrill`), not
`tuple[str, object]`, so the consuming loop needs no `type: ignore` casts.

**5. Extend the terminal payload additively.** New terminal fields (`deferred_questions`) use
`default_factory=list` so existing clients ignore them (JSON drops unknown keys). Leave the TS
twin unmodeled but add a NOTE mirroring the existing `turn_cost` precedent — silent drift bites
the next slice otherwise.

**6. Keep agent files < 500 lines by splitting on responsibility.** `phases.py` (pure replay,
"what next") / `responses.py` (pure turn builders, "how to render") / `engine.py` (orchestration
+ the two LLM calls, "when to invoke"). The seam also makes the deterministic turns testable
without touching Gemini.
