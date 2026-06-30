# Phase FSR-M6b: Priority feed mix (followed-source items get guaranteed slots first)

**Milestone:** M6 — Source/cluster onboarding UI + priority feed mix (feed-source revamp, `plans/prd.md`)
**Status:** Not started
**Estimated effort:** M

## Goal
`feed_assembly` gives **fresh followed-source items GUARANTEED slots first** (ahead of topic fill), then category top-stories fill the remainder of the ~30-reel feed; over-budget source items spill by a documented recency+importance rule; produce-cap headroom is revisited for the new mix. The whole allocation stays a **pure function over a fixture pool** so the priority + totals-30 invariants are unit-proven offline.

## Why this phase exists
The PRD thesis is that *follows are the personalization* (Decision #8, User Story 16): they must lead the feed, not be buried as importance-gated candidates. Today `agents/pipeline/feed_assembly.py` already (a) exempts source items from the produce gate and (b) fills `SLOT_KIND_SOURCE` slots in **Pass 1** up to each source category's *budget*, soft-rolling unfilled source budget into topics. What's MISSING for M6: source items only get their *budgeted* count, ordered at their category's sequence position — they are not **guaranteed priority** ahead of topic fill, there's no **over-budget spill rule**, and `produce_caps` headroom hasn't been revisited for a source-led mix. This phase closes exactly those gaps; it does NOT re-author the allocator.

## Cross-milestone dependency (assumed)
- **M1 / M5 / M6a are NOT prerequisites for the pure allocator work** — SP1–SP3 operate over a synthetic fixture pool (followed-source `CanonicalStory` list + scored topic candidates), so they run independently of the UI phase. The *live* coupling (real follows → real source stories → this allocator) is the LIVE-E2E residual.
- Consumes the existing shared-pool contract: `CanonicalStory` (source-origin domain stamping `youtube.com`/`x.com`), `ScoredCandidate`, `CategoryAllocation`, `score_and_classify_for_user` — all already present (no dependency on M2/M3/M4).

## Reuse (do NOT recreate)
- `agents/pipeline/feed_assembly.py` — `assemble_user_feed`, `_fill_source_slots`, `_take_top_qualifying`, the Pass 1–4 structure, `FEED_SLOT_BUDGET = 30`, `SLOT_KIND_SOURCE`, the don't-repeat (§3.8) + within-feed dedup. **Modify the priority/spill behavior; keep the contract + idempotent `write_daily_feed`.**
- `agents/pipeline/produce_caps.py` — `compute_category_produce_caps`, `headroom_multiplier`. Revisit the multiplier; do not rewrite the caps model.
- Existing tests: `tests/agents/pipeline/test_feed_assembly.py`, `test_produce_caps.py` — mirror their fixture style.

## Sub-phases

### Sub-phase 1: Guaranteed source-priority assembly (pure)
- **Files touched:** `agents/pipeline/feed_assembly.py` (the Pass 1 / ordering logic in `assemble_user_feed` + `_fill_source_slots`).
- **What ships:** fresh followed-source items take **guaranteed slots first** — before topic categories are filled — capped at the feed budget, then category top-`Score` stories fill the remainder to 30. Source slots lead the emitted order (or sit at the user's source-sequence position per the owner's "guaranteed first" intent — pin in Open Q1). Preserve: don't-repeat, within-feed dedup, a story qualifying for both a source and a topic slot is placed **once with source winning**, and the existing soft-roll when a source produced nothing.
- **Definition of done:** unit tests over the PURE `assemble_user_feed` against a fixture pool: with K fresh followed-source items (K < 30), **all K occupy priority slots and topic stories fill 30−K** (the test asserts the source items LEAD / are all present — Rule 9: it fails if a topic story displaces a fresh follow); a story present in both the source list and a topic bucket appears exactly once and as a `SLOT_KIND_SOURCE` slot; zero followed sources → a full 30 of category news (never empty); the feed still totals `min(sum(budgets), 30)`.
- **Dependencies:** none. INDEPENDENT of SP2.

### Sub-phase 2: Over-budget source spill rule (pure)
- **Files touched:** `agents/pipeline/feed_assembly.py` (spill ordering in `_fill_source_slots` / a helper).
- **What ships:** when fresh followed-source items **exceed** the feed budget (or the source allowance), they are ranked and capped by a documented **recency + importance** rule (most-recent / most-important first), and the overflow is dropped (not spilled into a later day in this phase). The rule is a single documented function so the cap is deterministic and testable.
- **Definition of done:** unit test: given >30 fresh source items, exactly 30 are kept and they are the top-30 by the documented recency+importance ordering (the test asserts a known low-rank item is dropped and a known high-rank item is kept — fails if the cap is arbitrary/insertion-order); the ordering is deterministic on ties (stable tiebreak by story id). Document the chosen rule inline + in the phase report.
- **Dependencies:** SP1 (shares `_fill_source_slots`; SEQUENTIAL — same region).

### Sub-phase 3: Produce-cap headroom for the source-led mix (pure)
- **Files touched:** `agents/pipeline/produce_caps.py` (the `headroom_multiplier` default / source-category handling), `agents/pipeline/feed_assembly.py` (only if a cap-related constant is shared).
- **What ships:** revisit `headroom_multiplier` (PRD: currently 1.0; phase-5d noted 1.5) so the render pool is large enough that, after quality-gate attrition, each category still fills its real budget under the new source-led mix — without over-producing. Document why the chosen value is right for a feed that leads with source items.
- **Definition of done:** unit test asserting `compute_category_produce_caps` at the chosen multiplier yields a render-pool size that covers the largest user budget after a representative gate pass-rate (the test pins the *reason* — e.g. demand 4 at the multiplier survives a ~60% gate to still fill 4 — not just a number); the final feed remains capped at the true `allocation_slot_count` by `feed_assembly` (no feed inflation). Existing `produce_caps` invariants (max-over-users, drop-uncpicked-categories) still pass.
- **Dependencies:** INDEPENDENT of SP1/SP2 (separate file/concern).

### Sub-phase 4: Source-led mix integration test + regression guard
- **Files touched:** `tests/agents/pipeline/test_feed_assembly.py` (new source-priority cases), `tests/agents/pipeline/test_produce_caps.py` (headroom case).
- **What ships:** a fixture-driven integration test exercising the full `assemble_user_feed` path end-to-end over a synthetic pool that combines: fresh followed-source items (under- and over-budget), topic candidates, a prior feed (don't-repeat), and a both-source-and-topic story — asserting the composed M6 invariant (**fresh follows lead, news fills to 30, no dupes, no repeats**) in one place so a future change can't silently regress the thesis. Plus the regression-guard assertions wiring SP1–SP3 together.
- **Definition of done:** the integration test asserts, in a single scenario, that fresh followed-source items occupy the leading slots, category top-stories fill exactly to 30, no story repeats a prior feed, no story appears twice, and the both-eligible story is a single source slot. `pytest tests/agents/pipeline/test_feed_assembly.py tests/agents/pipeline/test_produce_caps.py` is green. **The test must fail if source items stop leading** (the load-bearing M6 product thesis).
- **Dependencies:** SP1, SP2, SP3.

## Phase-level definition of done
`feed_assembly.assemble_user_feed` (pure over a fixture pool) gives fresh followed-source items guaranteed slots first, fills the remainder to 30 with category top-stories, caps over-budget source items by a documented recency+importance rule, preserves don't-repeat + dedup + soft-roll, and `produce_caps` headroom is set for the source-led mix — all proven by `pytest tests/agents/pipeline/test_feed_assembly.py tests/agents/pipeline/test_produce_caps.py`. **LIVE-E2E residual (deferred):** a real batch producing a real `daily_feeds` that leads with real followed-source items.

## Out of scope
- The onboarding **UI** (Phase FSR-M6a).
- The **ranking `β` raise** and importance model (M3 — separate milestone).
- News ingestion / themes / trusted-outlet fetch (M2, M4).
- Spilling over-budget source items into a **later day** (this phase drops overflow; cross-day carry is a later enhancement).
- Live batch execution against a real DB/GDELT (the LIVE-E2E residual).

## Open questions
1. **"Guaranteed first" ordering:** do source slots lead the WHOLE feed (positions 1..K), or sit at the user's source-category sequence position while still being guaranteed-present? PRD says "guaranteed slots first"; the existing allocator emits per the user's sequence. Assumed: guaranteed **presence + priority over topic fill**, emitted at the source category's sequence position (least disruptive to phase-5a sequencing). Pin in SP1; the test asserts presence+priority regardless.
2. **Over-budget spill rule weights:** exact recency-vs-importance tiebreak (PRD flags this as a tuning decision). Assumed: recency primary, importance secondary, story-id stable tiebreak. Pin in SP2.
3. **Headroom value:** 1.5 (phase-5d note) vs another value. Assumed 1.5 unless the SP3 pass-rate math says otherwise.

## Self-critique

**Product lens:** PASS. The single load-bearing M6 feed-mix claim — *fresh follows lead, news fills to 30* (User Stories 16–17, 21; PRD Decision #8) — is asserted in SP1 and re-asserted as a regression guard in SP4, exactly where the PRD says the source-first thesis "must carry uniqueness." No scope creep: ranking β, importance, ingestion are explicitly deferred to their milestones. Zero-follow path (User Story 21 — never empty) is a DoD in SP1.

**Engineering lens:** PASS. Every DoD is a pure-function unit test over an injected fixture pool — fresh-context verifiable, no live DB/GDELT, conformant to the PRD's offline constraint and the existing `feed_assembly`/`produce_caps` test style. No new service or table — modifies pure allocation logic only. SP4 (the lock-in/integration step) correctly comes last. SP1 and SP2 both touch `_fill_source_slots`, so they are NOT independent — marked SEQUENTIAL. SP3 is a genuinely separate concern (produce-cap headroom, different file) — INDEPENDENT, not padding.

**Risk lens:** PASS. File-boundary conflict: SP1 and SP2 both edit `feed_assembly.py` `_fill_source_slots` → **explicit SP1→SP2 dependency** so `/run-phase` serializes. SP4 edits only test files. Test coverage: every behavioral DoD names a test that fails when the rule breaks (source items must lead; a known item must drop on over-budget; headroom must cover budget after attrition) — Rule 9. Reversibility: pure-logic changes to a precomputed allocator — **no migration, no public API, no data deletion**; a regression is a code revert. Painting-into-a-corner: SP1 establishes priority, SP2 adds the cap on top, SP3 sizes the upstream pool, SP4 verifies the composite — each builds on the prior's state; no reorder needed.

**Irreversible sub-phases:** none (pure-function changes to a precomputed feed allocator + tests; no schema/API/data change).
