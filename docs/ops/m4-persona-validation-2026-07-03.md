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

## RESUME — day-1 batch SUCCEEDED (2026-07-03 evening, credits restored)

Credits were topped up; the exact parked batch command re-ran 23:36–00:13 UTC and
**completed**. Before → after (vs the depleted run above): produced 0 → **13 reels**,
feeds_written 0 → **8 users** (all 3 personas included), `story_interests` tags
persisted 0 → **45 rows** → **the 3-day census clock has STARTED** (day 1 of 3).

### Batch numbers (from the structured log)

- candidate=366 (BigQuery, 9 interests / 47 predicates), all 366 gate-passed;
  203/366 dropped at `theme_category_no_whitelisted_theme` (same shape as the failed
  run's 204 — still worth a fill-quality look).
- 21 write-phase attempts → 13 produced. The gap is honest quality gating: 4
  verification halts (digest unsupported/contradicted vs its single source — never
  published) + 4 other pre-publish drops (scoring/selection).
- Produce caps confirmed the recorded multi-niche defect in the wild: caps resolve to
  the MAX single-section demand per category (`sport: 7` vs cricket's 21 joint sport
  slots; `headroom_multiplier: 1.0`), so feeds landed at **13/13/12 slots** (founder/
  cricket/chip), not 26+4. Under-fill, not breakage — every filled slot carries
  correct section metadata. Cap tuning stays deferred to the multi-day read.
- `ONLY_USER_EMAIL` scopes ingestion+produce but NOT feed assembly (known 2026-06-18
  gotcha): 5 other allocated users also got (small) feeds from the shared pool.
  Disclosed, not fought — no personas' data affected.

### Same-day census (day 1 of 3)

**One batch, two census "days":** the run straddled UTC midnight, so its tags split
across pull-day keys 2026-07-03 (11 rows) and 2026-07-04 (34 rows). The census
window read (`--days 3`, archived at `docs/ops/evidence/m4-day1/census-day1.json`)
therefore shows 27.8% (5/18 cells) — it charges each interest a miss on BOTH
half-days. Treating the single run as ONE pull-day (the honest day-1 number):

| persona | direct-niche hits | day-1 rate |
|---|---|---|
| founder | ai.foundation-models: 2, tech.developer-tools: 3, business.venture-capital: 0 | 2/3 (66.7%) |
| cricket | sport.cricket.india-team: 4, sport.cricket.world-cup: 3, sport.cricket.ipl: 0 | 2/3 (66.7%) |
| chip | geopolitics.chip-export-controls: 1, tech.semiconductors.nvidia: 0, tech.semiconductors.tsmc: 0 | 1/3 (33.3%) |
| **overall** | 5 of 9 niches drew ≥1 direct hit | **55.6% vs 60% — just below, day 1 only** |

Instrument note for the multi-day read-off: schedule days 2–3 batches to FINISH
before UTC midnight, or the census will keep splitting runs into half-days and
deflating the windowed rate.

### Browser walkthrough — niche sections seen in the wild (all 3 personas)

Real chain, no PostgREST stubs, no DB surgery: `next dev` :3100 against prod
Supabase, fresh persona sessions (magiclink → verify_otp recipe), puppeteer-core +
system Chrome. **One disclosed harness shim:** the page clock was pinned back 100
minutes because "today UTC" rolled to 07-04 minutes after the batch wrote
`feed_date=2026-07-03` (the long-known UTC feed-date gotcha) — the client then
requested 2026-07-03 exactly as a real user did before midnight. Verified
(screenshots in `docs/ops/evidence/m4-day1/`):

- **Direct-niche sections render in user vocabulary**: founder "Foundation models — 2"
  / "Dev tools — 1"; cricket "Team India cricket — 7"; chip "Chip export controls — 5"
  (`.seg-chip`).
- **Honest fallback labels** (`[data-testid="section-fallback"]`): "Nothing new in
  Venture capital today — here's Business & Markets" (founder), "Nothing new in Team
  India cricket today — here's Cricket" (cricket, over an England-T20-final story
  climbed from the Cricket parent), "Nothing new in TSMC today — here's Tech &
  Science" (chip, a full 3-slot level-2 climb).
- **Beyond your bubble** renders for all 3 (7/6/4 slots).

**Two real defects observed (recorded, not glossed):**

1. **ReelEmpty is a navigation dead end.** The library/tab bar only opens from the
   wordmark inside a story stage; with an empty reel there is NO path to Archive /
   Sources / Settings — the user is stuck on "Your briefing is being prepared" +
   Refresh. Also blocks the Archive route to yesterday's briefing (which would have
   made the clock shim unnecessary). Candidate follow-on slice.
2. **UTC-midnight batch straddle** (pipeline+census+client coupling): an evening
   local-time batch writes a `feed_date` that the Today reel stops requesting at
   UTC midnight, and the census splits its tags across two pull-days. Operational
   for now (run batches earlier); the UTC feed-date gotcha is long-recorded.

## Acceptance-criteria scoreboard (after day-1 resume)

| criterion | state |
|---|---|
| 3 personas interview → batch → feed rendering | DONE for day 1 — all 3 personas: interview, allocation, paid batch, feed rendered in-browser (disclosed shims: `user_onboarded_at` routing stamp, walkthrough clock pin) |
| Hit rate vs ≥60% over ≥3 real days | IN PROGRESS — day 2 of 3 complete: **day-2 100.0% overall** (founder 100 / cricket 100 / chip 100), up from day-1 55.6%. See "Day-2 census — 2026-07-07" below; still needs a 3rd pull-day before go/no-go |
| Tuning pass documented w/ before/after | DONE — interview slug normalization; all 3 personas re-run (0/3 roots-only after, was 2/3) |
| Honest-fallback + beyond-bubble seen in the wild | DONE — screenshots in `docs/ops/evidence/m4-day1/`; fallback labels + beyond-bubble verified for all 3 personas |
| Watch-items baselined | DONE — taps 4–5, wall 5.2–13.4 s, <$0.01 LLM/onboarding |
| Silently-skipped steps called out | DONE — see "Honest deviations", the resume section's disclosed shims, + this table |
| Go/no-go recorded | PENDING — day 2 of 3 done (07-07); needs one more successful pull-day before the go/no-go read-off. No verdict yet |

---

# Day-2 census — 2026-07-07

Second real pull-day of the ≥3-day go/no-go clock (day 1 was the 07-03/04 straddle run).
Today's production batch (`feed_date=2026-07-07`) had **already run** when this measurement
began — no batch was launched here, no paid Gemini/BigQuery spend. It finished
**14:26:35 UTC**, comfortably before UTC midnight, so — unlike day 1 — it did **not**
straddle midnight and the census needs no half-day reconciliation
(`docs/solutions/operational-gotchas/batch-straddling-utc-midnight-splits-pull-day.md`).
Batch produced 67 stories, wrote feeds for 10 users; all 3 personas got **30/30 rows**
with no manual DB surgery. Batch log:
`scratchpad/live_batch_2026-07-07.log`.

## Direct-niche hit rate — the go/no-go metric (census, same method as day 1)

Measured exactly as day 1: for each persona, the fraction of their 3 followed
micro-interests that drew **≥1 direct `story_interests` tag** on the pull-day, via
`scripts/coverage_census.py --start-date 2026-07-07 --end-date 2026-07-07`. Machine JSON
archived at `docs/ops/evidence/m4-day2/census-day2.json` (written locally; per the day-2
task scope only this report file is committed).

| persona | direct-niche hits (per-niche tag counts) | day-2 rate | day-1 rate |
|---|---|---|---|
| founder | ai.foundation-models: 8, tech.developer-tools: 4, business.venture-capital: 1 | **3/3 (100%)** | 2/3 (66.7%) |
| cricket | sport.cricket.world-cup: 6, sport.cricket.india-team: 4, sport.cricket.ipl: 3 | **3/3 (100%)** | 2/3 (66.7%) |
| chip | geopolitics.chip-export-controls: 4, tech.semiconductors.nvidia: 4, tech.semiconductors.tsmc: 3 | **3/3 (100%)** | 1/3 (33.3%) |
| **overall** | 9 of 9 niches drew ≥1 direct hit | **100.0% vs 60% → PASS** | 55.6% |

**Day-1 → day-2 movement: 55.6% → 100.0% (+44.4 pts).** Every niche that was DRY on day 1
(venture-capital, ipl, nvidia, tsmc) drew direct hits today; no niche regressed. This is
one strong day, not a verdict — the go/no-go still needs a 3rd real pull-day, and one
outlier day (either direction) should not be over-read.

## Slot-fill composition — a separate, lower number (recorded, not conflated) (Rule 12)

The census above asks "does each niche section draw ≥1 direct story into the pool?"
(the ≥60% metric). A **different** question — "what fraction of the 30 rendered slots
are direct-leaf fills?" — reads `daily_feeds.feed_fallback_source_level` per persona and
gives a much lower number, so it is reported separately, NOT blended into the headline:

| persona | direct-leaf (L0) | climbed (L≥1) | beyond-bubble | source | direct-of-30 |
|---|---|---|---|---|---|
| founder | 11 | 10 | 9 | 0 | 36.7% |
| cricket | 9 | 8 | 13 | 0 | 30.0% |
| chip | 3 | 18 | 9 | 0 | 10.0% |
| **overall** | 23 | 36 | 31 | 0 | **25.6% (23/90)** |

Reconciliation: a niche can pass the census (≥1 direct tag exists pool-wide) yet still see
its **section climb** for most of its slot budget, because the leaf-tagged stories that
survive into *this persona's* ranked candidate set (after cross-section dedup) are fewer
than the section's slot budget — so the ladder honestly climbs for the remainder. This is
under-fill of the leaf, **not breakage**: every climbed slot carries a correct
ancestor-named fallback label (cited below), and personas are still full at 30/30. It is a
flag for the day-3 **tuning read** (per-interest caps / section budgets vs available
leaf-tagged supply), consistent with the multi-niche produce-cap defect recorded on day 1.
No tuning pass is performed on day 2 per scope.

## Edge coverage in the wild — cited from the persisted 2026-07-07 `daily_feeds` rows

These are the exact rows the section renderer (slice #8, screenshot-verified in-browser on
day 1) consumes; day-2 edge coverage is evidenced from the persisted feed rows rather than
a fresh browser capture.

- **Honest fallback, level 1 (parent climb):** founder `Venture capital` section
  (`business.venture-capital`) filled from `business` at positions 9–14 — renders as
  "Nothing new in Venture capital … here's Business & Markets."
- **Honest fallback, level 2 (grandparent climb):** chip `TSMC` section
  (`tech.semiconductors.tsmc`) filled from `tech` at positions 6–8; cricket
  `Cricket World Cup` section (`sport.cricket.world-cup`) filled from `sport` at
  positions 14–17. Both are full two-level climbs with the leaf-named header preserved.
- **Beyond your bubble** renders for all 3 personas (founder positions 22–30 = 9 slots,
  cricket 18–30 = 13 slots, chip 22–30 = 9 slots), `feed_section_label='Beyond your bubble'`.

## Residual / honesty notes (day 2)

- **Census checklist not updated.** `docs/ops/coverage-census-day1-2026-07-03.md` still
  shows the "2nd successful batch day" box unchecked; the day-2 task scope committed only
  this report file, so that checklist was intentionally left untouched — flagged here so it
  is not silently stale.
- **Days 07-04/05/06 had no persona pull-day** in this measurement window (the census reads
  07-07 as the 2nd counted day after the 07-03 day-1 run). Day 3 is the next successful
  persona batch.
- **No browser walkthrough re-run on day 2** (not in scope); render path was screenshot-
  verified day 1 and the underlying rows are cited above.
