# M4 persona validation — day-1 run (2026-07-03)

Slice #10: the end-to-end proof for the founder / cricket-obsessive / chip-industry-nerd
personas, and the evidence base for the ≥ 60% direct-niche hit-rate go/no-go. **Status:
day-1 PARTIAL by construction** — niche-sectioned assembly (slice #7, migration 0027)
went live 2026-07-03, so the ≥ 3-real-pull-days hit-rate criterion and the final
go/no-go structurally cannot complete before 2026-07-05/06 (tracked in #11; checklist in
`docs/ops/coverage-census-day1-2026-07-03.md`). Nothing below is faked or glossed
(Rule 12); every gap is named.

## The E2E chain as run (per persona)

1. **Interview** — real Gemini turns against the production `POST /api/interview/turn`
   contract on a local worker (`scripts/e2e/interview_persona_driver.py`, committed):
   real persona JWT (admin magiclink → verify_otp), deterministic tap policy playing
   the persona, watch-items measured from the typed `turn_cost` in each response.
2. **Profile** — the seeded slice #3 profiles (`scripts/seed_personas.py`, the interview
   persistence contract's exact stand-in) remain the profiles under validation; the
   driver never writes profile rows, keeping the frozen 9-interest census denominator.
3. **Allocation** — slice #6 runner `scripts/allocate_niche_sections.py` per persona
   (cricket was already allocated by slice #6; founder + chip allocated today —
   10 rows / 30 slots each: 3 named niche sections + beyond-bubble reserve + source slots).
4. **Batch** — one paid `scripts/run_live_batch.py` run scoped to exactly the 3 personas
   (`ONLY_USER_EMAIL` now accepts comma-separated emails — the only batch-code change),
   `PRODUCE_CAP_HEADROOM=1.0 MAX_PRODUCE=0 POSTER_MODE=sync INGEST_SOURCE=bigquery`,
   feed_date 2026-07-03.
5. **Census** — `scripts/coverage_census.py` over the 2026-07-03 pull-day (day-1 baseline).
6. **Browser** — real `next dev` against prod Supabase, real persona sessions injected
   (no PostgREST stubs), puppeteer walkthrough of the rendered reel sections.

### Honest deviations (Rule 12 — read these before trusting the numbers)

- **`users.user_onboarded_at` was stamped directly** for the 3 personas (routing flag
  only). Reason: completing onboarding in-app runs the Build-My-30 stage, whose persister
  (`src/lib/feedAllocation.ts`) writes COARSE allocation rows and would clobber the niche
  allocation under validation — the known #12 gap (Build-My-30 has no niche editing
  semantics yet). The FEED itself required no DB surgery: every `daily_feeds` row was
  written by the pipeline. This is a routing-gate bypass, disclosed, not feed surgery.
- **Interview terminal lists were NOT persisted to prod.** The driver measures and
  compares; persisting divergent LLM-minted slugs would silently change the 9-interest
  denominator the 3-day census (#11) is already scored against. Convergence is reported
  below instead.
- **Personas have no followed YouTube/X sources**, so their 4 source slots legitimately
  stay empty (RUN_SOURCES off) — feeds top out at 26 topic slots by design.

## Interview leg — results + tuning pass

### Day-1 BEFORE tuning (engine at 95c319f)

| persona | completed | terminal quality | taps | turns | wall | LLM tokens |
|---|---|---|---|---|---|---|
| founder | yes | **roots-only fallback** (business, tech — extraction failed) | 5 | 2 | 10.6 s | 3,254 |
| cricket | yes | 1 niche (`sport.cricket.india`) of 3 expressed | 4 | 3 | 6.7 s | 2,985 |
| chip | yes | **roots-only fallback** (tech) | 4 | 2 | 11.7 s | 3,495 |

Root cause (worker logs, `interview_interest_dropped`): Gemini proposes the RIGHT
concepts with **underscore separators** (`ai.foundation_models`,
`geopolitics.tsmc.chip_foundry`); the shape guard rejected them, and the engine parked
users on roots-only — the exact failure user story #10 forbids ("typed free-text must
become a real tracked niche").

### Tuning pass (the one documented change)

`agents/interview/guards.py` — `validate_and_dedup` now normalizes model slug
separators (`_`/whitespace → `-`, collapse repeats, strip edge dashes) BEFORE shape
validation (`_normalize_slug`). Deterministic spelling variance is folded by code
(Rule 5); genuinely malformed slugs are still dropped. Unit-locked by 3 new tests in
`tests/agents/interview/test_engine.py` (happy / failure / edge).

### AFTER tuning — all 3 personas re-run (none skipped)

| persona | completed | roots-only | terminal slugs | converged w/ seed | taps | turns | wall | LLM tokens |
|---|---|---|---|---|---|---|---|---|
| founder | yes | **no** | ai.foundation-models, business, tech | `ai.foundation-models` (exact) | 5 | 2 | 6.3 s | 2,313 |
| cricket | yes | no | sport.cricket.india | none exact (near: india-team) | 4 | 3 | 5.2 s | 2,684 |
| chip | yes | **no** | geopolitics.tsmc, tech | none exact (near: tsmc niche) | 4 | 2 | 13.4 s | 4,011 |

Before → after: roots-only fallback 2/3 → **0/3**; exact-slug convergence 0 → 1;
every persona now lands at least one real micro-interest node.

**Residual interview defect (recorded, not glossed):** the engine terminates after
~2–3 turns — after the FIRST typed niche — so a persona with 3 niches never gets to
express the other two before terminal. Terminal lists are therefore 1–3 interests, not
the seeded 3+. This is an engine termination-policy issue (model chooses `terminate`
too eagerly after a free-text turn), NOT a guard issue; candidate follow-on slice.
Convergence-by-slug is also soft (`sport.cricket.india` vs `sport.cricket.india-team`)
— two spellings of one niche mint two nodes; same follow-on.

### Watch-items (baseline numbers, after-tuning runs)

- **Interview completion time:** 5.2–13.4 s wall (3 personas; local worker, real Gemini).
  Well inside the ≤ 3 min target — but note the early-termination defect above means
  fewer turns than a complete 3-niche interview would take.
- **Taps per persona:** 4–5 (incl. terminal confirm; target ~15, hard cap 18).
- **LLM cost per completed onboarding:** 2,313–4,011 total tokens on `gemini-2.5-flash`
  (≈ 2–3 LLM turns). At list price (input $0.30/M, output incl. thinking $2.50/M) this
  is **under $0.01 per onboarding** — cost is a non-issue at MVP scale.

## Batch + census — day-1 baseline

The persona-scoped paid batch ran 2026-07-03 ~20:36–20:52 UTC
(`RUN_LIVE_BATCH=1 ONLY_USER_EMAIL=<3 personas> PRODUCE_CAP_HEADROOM=1.0 MAX_PRODUCE=0
POSTER_MODE=sync`). What happened, from the structured log:

- **Ingestion worked end-to-end**: one batched BigQuery pass over all 9 persona
  micro-interests (47 term predicates) → **368 candidate stories** fetched, bodies
  extracted, all 368 passed the produce gate. The niche-ingestion leg is healthy.
- **Production produced 0 reels**: partway through the run the Gemini project's
  **prepayment credits depleted** — every TTS chunk and then every LLM render stage
  returned `429 RESOURCE_EXHAUSTED` ("Your prepayment credits are depleted"), all 12+
  render attempts failed, `produced_story_count=0`, `feeds_written=0`. Confirmed still
  depleted at 21:0x UTC with a direct probe (same 429 on a 3-token call). Same blocker
  as the 2026-06-17 incident (memory: resolved then by a top-up).
- Consequently **no `story_interests` tags persisted for 2026-07-03** (tags persist
  per-PRODUCED-story — see `docs/solutions/architecture-patterns/story-interests-holds-produced-not-ingested.md`),
  so the census still reads `0/0 (not computable)` and **the 3-day clock has NOT
  started**. The `2026-07-03 batch persisted` checkbox in
  `docs/ops/coverage-census-day1-2026-07-03.md` stays UNCHECKED.

**Unblock (human action):** top up Gemini prepay credits (AI Studio → project billing),
then re-run the exact batch command above — produce-once is safe (no feeds were
written) and allocations/profiles are already in place. Each successful batch day
after the top-up counts as one census pull-day.

Also observed for the eventual tuning read (recorded now so it isn't lost):
- The per-category produce cap resolves to the MAX single-section slot count per
  category (`sport: 7`), while cricket's three sport sections jointly demand 21 slots —
  the cap machinery predates niche sections and will under-provision multi-niche
  categories. Watch this on the first successful day before tuning caps.
- 204 of 368 candidates logged `theme_category_no_whitelisted_theme` — worth a look
  when reading day-1 fill quality.

## Browser verification — harness proven; feed render pending the batch

Real-data walkthrough (no PostgREST stubs): `next dev` on :3100 against prod Supabase,
real persona sessions minted via admin magiclink → `verify_otp` and injected as
`sb-<ref>-auth-token`, puppeteer-core + system Chrome (recipe:
`docs/solutions/tooling-decisions/browser-verify-static-export-spa.md`). Verified today:

- All 3 personas authenticate and route PAST onboarding into the app shell.
- With `feeds_written=0` each correctly shows the honest **ReelEmpty** state ("Your
  briefing is being prepared") — no crash, no stale/shared feed (the 2026-06-30
  gate/fallback fixes hold for fresh niche users).

The niche-section / fallback-label / beyond-bubble screenshot evidence therefore
**cannot exist yet** — it requires the first successful batch. The walkthrough script
is ready to re-run as-is (session-mint + puppeteer recipe above; selectors from
`tests/e2e/reelSections.e2e.mjs`: `.seg-chip`, `[data-testid="section-fallback"]`).

## Go/no-go

**PENDING — structurally not decidable today, and now gated on billing.** The ≥ 60%
read-off needs ≥ 3 real pull-days on/after the personas' seed date (2026-07-03).
Because today's batch produced nothing (credits), the earliest 3 pull-days are the
first 3 successful batch days after a top-up — **2026-07-06 at the earliest** if
batches run 07-04/05/06 (issue #11's wait condition shifts accordingly). If the
multi-day rate lands structurally below 60%, the recorded recommendation is the PRD's
cheap pivot: coarser niches (interview drills one level less) — interview + ladder
machinery unchanged.

## Acceptance-criteria scoreboard (day-1 honest state)

| criterion | state |
|---|---|
| 3 personas interview → batch → feed rendering | PARTIAL — interview+allocation done for all 3 (no DB surgery for feeds; disclosed `user_onboarded_at` routing stamp); batch blocked by Gemini credits, no feeds yet |
| Hit rate vs ≥60% over ≥3 real days | NOT STARTED — no pull-day has persisted tags yet; clock starts with first post-top-up batch |
| Tuning pass documented w/ before/after | DONE — interview slug normalization; all 3 personas re-run (0/3 roots-only after, was 2/3) |
| Honest-fallback + beyond-bubble seen in the wild | PENDING — needs first produced feed |
| Watch-items baselined | DONE — taps 4–5, wall 5.2–13.4 s, <$0.01 LLM/onboarding |
| Silently-skipped steps called out | DONE — see "Honest deviations" + this table |
| Go/no-go recorded | PENDING (structural + billing) |
