# Phase 6b: Learned feed ordering

**Milestone:** M6 — Discovery agent & learned ordering
**Status:** Not started
**Estimated effort:** M

## Goal
Move feed **ordering** from manual to engagement-learned: surface the topics/sources a user actually engages with earlier, using **watch-completion + questions-asked + follow/unfollow only** (never gestures), applied **after** the pinned-first allocation (5e), with a cold-start fallback to manual prefs and a diversity floor so nothing gets starved.

## Why this phase exists
Spec §6 (phase 2 of personalization): start manual, then let signals refine ordering. Master plan Decision #8 keeps this heuristic (no ML). **Critical correction (C2):** spec §6 lists "gesture usage" as a learning signal, which violates the locked swipe-is-navigation rule (picker spec §1, pers. spec §8). This phase **drops gestures** from the learning loop — it learns from completion/questions/follows only.

## Context the sub-agents need
- **Existing signal loop:** M1 shipped a bounded/decayed signal→weight loop per `reference/ranking-spec.md`, with `player_signals` (table) + `src/lib/signals.ts`. `player_signal_event` is an enum (includes gesture events). This phase **extends** that loop to learn ordering, reusing its decay machinery.
- **Allocation:** Phase 5e's `agents/pipeline/feed_assembly.py` produces `daily_feeds` (with `feed_position`) via pinned-first allocation. This phase adds a final **ordering** stage that sorts within the allocation without breaking pinned-first.
- **C2 is a hard rule:** gesture/swipe events must be **excluded** from the learning aggregation. A test must fail if they leak in.
- **No ML** (Decision #8) — a transparent heuristic over decayed engagement weights.
- **Server-side:** ordering runs in `agents/pipeline`, written into `daily_feeds`.

## Sub-phases

### Sub-phase 1: Non-gesture engagement aggregation
- **Files touched:** `src/lib/signals.ts` (extend), `agents/pipeline/signal_weights.py` (new).
- **What ships:** aggregation of per-(user, category) and per-(user, source) engagement from **watch-completion + questions-asked + follow/unfollow only**, into a time-decayed weight (reusing the `ranking-spec` decay). Gesture/swipe `player_signal_event`s are **explicitly excluded**.
- **Definition of done:** given a `player_signals` fixture that **includes** gesture rows, the aggregation includes completion/question/follow events and **excludes** gesture events (Rule 9: a dedicated test fails if a gesture event changes the weight); decay is applied so older signals weigh less. Pytest.
- **Dependencies:** none (extends the M1 loop).

### Sub-phase 2: Heuristic ordering model
- **Files touched:** `agents/pipeline/feed_ordering.py` (new), `reference/ranking-spec.md` (extend with the ordering section).
- **What ships:** `order_feed(slots, weights)` — orders the allocated slots by learned per-user engagement weight (higher-engagement categories/sources earlier) with **no hard-coded category order** (changing the weights changes the order); deterministic tie-breaks.
- **Definition of done:** a user whose engagement concentrates on `ai`/AI-chips gets those slots ordered earlier than a flat-baseline user; **no category is hard-pinned** (a test that permutes weights permutes the output order); deterministic for fixed weights. Pytest.
- **Dependencies:** Sub-phase 1.

### Sub-phase 3: Wire ordering into feed assembly
- **Files touched:** `agents/pipeline/feed_assembly.py` (extend), `trigger/dailyPipeline.ts` (ensure the ordering stage runs).
- **What ships:** `feed_assembly` applies `order_feed` as the **final** stage after 5e's pinned-first allocation, writing `daily_feeds.feed_position` by learned order **while preserving the allocation** (sources still claim their slots first; ordering only sorts within the allocated set, it does not re-allocate).
- **Definition of done:** `daily_feeds` rows get `feed_position` reflecting learned order while pinned-first is preserved (followed-source slots are not dropped or out-prioritized by ordering); an integration test over seeded signals + prefs shows source slots present and ordered sensibly. Pytest with a seeded test DB.
- **Dependencies:** Sub-phases 1, 2; Phase 5e SP2 (allocation).

### Sub-phase 4: Cold-start fallback + diversity floor
- **Files touched:** `agents/pipeline/feed_ordering.py` (extend), `agents/pipeline/feed_assembly.py` (apply guards).
- **What ships:** a cold-start fallback (zero/sparse signals → fall back to the manual 5e prefs order, no learned reordering) and a **diversity floor** guard ensuring a heavily-weighted category cannot push a followed source or a chosen topic entirely out of the 30 (a minimum representation per active follow/topic).
- **Definition of done:** a zero-signal user's feed orders by manual 5e prefs (learned ordering is a no-op); a synthetic user whose weights all concentrate on one category still keeps ≥1 slot for each followed source and a floor for other chosen topics (diversity floor enforced — a test that fails if a follow is fully starved). Pytest encoding both intents.
- **Dependencies:** Sub-phases 2, 3.

## Phase-level definition of done
Per-user feed ordering adapts to watch-completion / questions / follow signals (**not** gestures), applied after the pinned-first allocation, with a cold-start fallback to manual prefs and a diversity floor that prevents starving any followed source or chosen topic — and no category order is ever hard-coded. **Validated by:** the gesture-exclusion aggregation test; the weight-permutes-order test; the ordering-preserves-pinned-first integration test; the cold-start + diversity-floor tests.

## Out of scope
- **ML** ordering (Decision #8 — heuristic only).
- Re-allocating slots (that's 5e — this only orders within the allocation).
- New signal **capture** (the events already exist from M1/M2/M3; this only consumes them).
- Any use of gesture/swipe signals (explicitly excluded by C2).

## Open questions
1. **Decay half-life** for the ordering weights (reuse ranking-spec's, or a separate longer horizon for ordering?).
2. **Diversity floor values** — minimum slots per followed source / per chosen topic.
3. **Granularity** — order by category, by source, or per-story (recommend category+source weight, story within).
4. **Interaction with 5e presets** — does a strong manual preset (e.g. Power Feed) cap how much learning can reorder?

## Self-critique
**Product lens:** PASS — delivers spec §6's "start manual, then learn ordering" with the right signals, and the diversity floor protects the user's explicit choices (a followed source never silently vanishes). Honors the owner's "don't hard-code order" directive.
**Engineering lens:** PASS — extends the existing M1 signal→weight loop rather than building a parallel one (Rule 8), stays heuristic per Decision #8, and runs as a final stage that **preserves** 5e's allocation (separation of concerns: 5e allocates, 6b orders). The C2 gesture-exclusion is enforced by code + a failing test, not just documented. DoDs are pytest-verifiable.
**Risk lens:** PASS. No schema changes, no irreversible ops. The two real risks — **gestures leaking into learning** (violates C2) and **learning starving an explicit follow** — are each a first-class DoD with a test designed to fail on violation (Rule 9). Painting-into-a-corner: aggregation → model → wire → guards; the guards (SP4) are added after wiring so cold-start/floor are validated against the real assembly path. Within-phase overlap: `feed_ordering.py` (SP2, SP4) and `feed_assembly.py` (SP3, SP4) are sequential, dependencies marked.
**Irreversible sub-phases:** none.
