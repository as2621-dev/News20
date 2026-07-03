# Interview Onboarding Spec — Conversational Micro-Interest Extraction

**Why this doc exists:** single source of truth for the chat-interview contract between the SPA onboarding stage and the worker's interview engine, and for how the terminal output maps onto existing tables. `/to-issues` slices against it; builders test against it.
**When to update:** any change to the turn protocol, bubble guardrails, terminal schema, or persistence mapping. Update alongside `supabase-schema.md` and `api-contracts.md` when the migration lands.

## 1. Flow position

Replaces the `picker` stage in `OnboardingFlow.tsx`'s state machine:
`splash → email → wait_session → **interview** → loading → sources → build`.
The `sources` (M6a) and `build` stages are unchanged. `users.user_onboarded_at` stamps only at true flow end (`markOnboardingComplete()`) — never mid-interview (onboarding-gate rule, 2026-06-30).

## 2. Turn protocol (worker endpoint)

One turn per request; server-driven; client is a dumb renderer.

- **Request:** conversation state so far — ordered list of (question shown, bubbles offered, bubbles tapped, free-text entered). No server-side session; state travels with the request (worker stays stateless, Railway cold-start friendly).
- **Response, non-terminal:** next question text + generated bubbles.
- **Response, terminal:** the extracted micro-interest list (schema §4) for user confirmation.
- **Failure contract:** HTTP 200 always, typed retry/error body (mirrors the Q&A endpoint's graceful-failure contract in `prototype-port-map.md` §7). The client renders a retry bubble; a failed turn is never a dead-end screen.
- **Auth:** user JWT (same seam as `/feed/assemble-mine`).

## 3. Bubble guardrails

| Rule | Value |
|---|---|
| Turn 1 bubbles | the 8 fixed roots (no LLM call) |
| Bubbles per turn | ≤ 6 generated + always `not really / skip` + always `something else — type it` |
| Drill-down depth | ≤ 3 per lit-up root |
| Tap budget | ~15 taps target; engine must steer to terminal by ~18 |
| Time target | ≤ 3 min (success metric #2) |
| Model | Gemini Flash-class, structured JSON output, temperature low |

Free-text handling: attempt to map typed input to a root + slug; if unmappable, ask exactly one clarifying turn, then park as root-level interest (never silently dropped, never fabricated into a niche).

## 4. Terminal output schema (per micro-interest)

- `display_label` — the user's vocabulary, verbatim (drives feed section headers).
- `canonical_slug` — root-anchored dotted slug (e.g. `sport.cricket.ipl.auctions`), the convergence key across users.
- `ladder` — ordered parent chain from the tap path (e.g. `["sport", "sport.cricket", "sport.cricket.ipl"]`); this IS the fallback ladder.
- `search_anchor_terms` — ≥ 2 concrete anchor terms for the BigQuery batched query (feeds `interest_search_query`).
- `strict` — bool, maps to `profile_is_strict` (no fallback climb when true).

Validation before persistence: root-anchored slug parses; ≥ 2 anchor terms; interest traceable to a tap or typed text (nothing invented). Invalid items are re-asked or dropped loudly, never silently minted. Raw chat transcript is NOT persisted — only the terminal list + ladder.

## 5. Persistence mapping

| Interview output | Lands in |
|---|---|
| minted niche node (slug, label, parent, depth, `interest_search_query`) | `interests` (global, shared; upsert by slug — two users converge on one node) |
| user's pick + weight + strictness | `user_interest_profile` (deep rows re-admitted; existing depth-weight semantics) |
| per-user display label | with the profile row, NOT the node |
| section allocation (label + node ref + slot count) | `user_feed_allocation` (+ new nullable interest ref + text label; enum stays for backbone/`youtube`/`x`/beyond-bubble) |
| per-slot section + fallback level (batch output) | `daily_feeds` row metadata (client renders labels with zero inference) |

Skip-everything degenerate case: roots-only profile (FSR baseline) — feed works exactly as today.

## 6. Assembly consumption (summary — details in `ranking-spec.md` when M3 lands)

Niche sections fill direct-tagged first; climb the ladder one level at a time when short; every climbed slot is stamped with its fallback level so the UI can say *"Nothing new in IPL today — here's cricket."* Strict interests never climb. Whole-ladder-dry slots go to the beyond-bubble reserve (3–5 slots of importance-ranked backbone stories from un-picked roots). Followed-source priority slots (M6b) are untouched.
