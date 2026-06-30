# Progress: phase-fsr-m6b-priority-feed-mix

**Phase file:** plans/phase-fsr-m6b-priority-feed-mix.md
**Started / shipped:** 2026-06-30
**Branch:** claude/feed-source-revamp-plan-388edf

## Test-env note
- pytest = uv tool env `/root/.local/share/uv/tools/pytest/bin/python`.
- Offline-only (GDELT 403 / no DB creds) — pure allocator + fixtures, no live e2e.
- Baseline: ~18 pipeline tests fail on missing ffmpeg/PIL/datasketch deps
  (`test_acoustic_alignment`, `test_forced_alignment`, `test_orchestrator*` audio/poster,
  clustering near_dup/online collection errors). Verified identical on the clean tree
  (`git stash` → same 18 fail), so NOT this phase's regression.

## Sub-phase progress
- [x] SP1: Guaranteed source-priority assembly (pure) — COMPLETED
- [x] SP2: Over-budget source spill rule (recency+importance, deterministic) — COMPLETED
- [x] SP3: Produce-cap headroom for the source-led mix (1.0 → 1.5) — COMPLETED
- [x] SP4: Source-led mix integration test + regression guard — COMPLETED
- [x] M3 residual #2 (cluster_importance → compute_story_score) — CLOSED (assembly seam)
- [~] M3 residual #1 (score_clusters in daily_batch + cluster_store.upsert) — PARTIALLY
      CLOSED / surfaced (note in daily_batch) — see "M3 residuals" below

## STATUS: PHASE SHIPPED

### What shipped (per sub-phase)

**SP1 — guaranteed source-priority assembly** (`agents/pipeline/feed_assembly.py`).
`_fill_source_slots` gained a `guaranteed_cap` param (the feed budget) and now lifts each
budgeted source category's cap from "its source budget" to "all its eligible follows"
(still bounded by `guaranteed_cap`). `assemble_user_feed` passes `guaranteed_cap=total_target`,
so fresh followed-source items take guaranteed slots AHEAD of topic fill — a user who
budgeted youtube=1 but has 5 fresh follows still surfaces all 5 (PRD Decision #8). Topic
fill takes `total_target − source_filled` (clamped ≥0; when source fills the whole feed,
topics get nothing). Source soft-roll, within-feed dedup, §3.8 don't-repeat, source-wins-
on-dual-eligible, and never-empty-on-zero-follow all preserved. **Open Q1 pinned:**
guaranteed PRESENCE + PRIORITY over topic fill, emitted at the source category's sequence
position (least disruptive to phase-5a sequencing). `source_roll_slots` clamped ≥0 (source
fill can now exceed source budget).

**SP2 — over-budget spill rule** (`agents/pipeline/feed_assembly.py`). New pure helpers
`_source_recency_importance_sort_key` + `_rank_source_stories` rank a category's source
stories BEFORE the cap binds. **Documented rule (Open Q2 pinned):** recency PRIMARY (newer
`canonical_published_utc` first) → importance SECONDARY (higher `story_outlet_count` first)
→ story-id TIEBREAK (ascending) — fully deterministic, never insertion order. Overflow is
dropped this phase (no cross-day carry — out of scope).

**SP3 — produce-cap headroom** (`agents/pipeline/produce_caps.py`,
`agents/pipeline/daily_batch.py`). New `DEFAULT_HEADROOM_MULTIPLIER = 1.5` and made it the
default for both `compute_category_produce_caps(headroom_multiplier=…)` and
`run_daily_pipeline(produce_cap_headroom=…)`. **Documented reason (Open Q3 pinned):** the
source-led mix REDUCES the topic slots that need filling (follows pre-empt part of the
feed), so 1.5 (not 2.0) is the tighter-yet-sufficient over-provision: demand D=4 → render
ceil(4×1.5)=6 → at ~67% gate pass-rate ≥4 survive == real budget. 2.0 would over-produce
topic reels the source-led feed never shows. The feed is still re-capped at the true
`allocation_slot_count` by `feed_assembly` (no feed inflation).

**SP4 — integration + regression guard** (`tests/agents/pipeline/test_feed_assembly.py`,
`tests/agents/pipeline/test_produce_caps.py`). One end-to-end scenario asserting the
composed M6 invariant (fresh follows lead, news fills to 30, no dupes, no prior repeats,
dual story = single source slot) + a dedicated regression guard (`youtube` budget 1, 5
follows → all 5 lead) that reverts red if the per-source-budget cap is reintroduced. SP3
headroom test pins the pass-rate REASON, not just the number.

### M3 residuals
**#2 (CLOSED).** Threaded a SHARED `cluster_importance_by_story` map through the whole
scoring path: `assemble_daily_feeds` → `assemble_user_feed` → `score_and_classify_for_user`
→ `score_candidates_for_user` → `generate_fallback_candidates` → `score_stories_for_interest`
→ `compute_story_score(cluster_importance=…)`. A clustered story's Importance term is now its
E1 within-category-normalized score; an un-clustered story falls back to the raw outlet
count (Rule 3 — additive, empty/None map is byte-identical to pre-M3). Proven offline:
- `test_ranking.py::TestClusterImportanceThreadsThroughScorer` — clustered story uses E1,
  un-clustered falls back, no-map == all-unclustered.
- `test_feed_assembly.py::test_cluster_importance_threads_into_feed_scoring` +
  `::test_unclustered_story_scores_identically_without_cluster_importance` — same through the
  feed-assembly path.

**#1 (PARTIALLY CLOSED — surfaced, not faked, Rule 12).** The instruction was to call
`score_clusters` post-clustering in `daily_batch` and persist via `cluster_store.upsert`,
then build the cluster_importance map at the `cluster_id ↔ story_id` bridge. INVESTIGATION:
the shared-pool ONLINE clusterer (`clustering/online_clusterer.cluster_candidates` →
`StoryCluster` + the `story_clusters` table, M3a/M3b) is **NOT called anywhere in
`run_daily_pipeline` today** — ingestion uses the simpler URL+title `StoryClusterer`
(`agents/ingestion/dedup`) that produces `CanonicalStory` with **no `cluster_id`**. So there
is no clustered batch to `score_clusters`/upsert in the daily batch yet. Wiring it requires
the full embeddings + blocking + continuity-persistence + cluster↔story bridge — an entangled,
paid-Gemini change far beyond M6b's surgical scope. Per the phase brief's fallback ("do the
assembly-side threading at minimum and mark the daily_batch call as a residual with a clear
note — don't fake it"): the assembly seam is fully wired (residual #2), and `daily_batch`
carries an explicit residual note where the bridge would attach (`cluster_importance_by_story
= None` today, with the un-clustered raw-importance fallback used — no fake values). This is a
LIVE-pipeline residual tracked here.

### Files changed
- `agents/pipeline/feed_assembly.py` — SP1 guaranteed source priority (`_fill_source_slots`
  `guaranteed_cap`) + SP2 spill rule (`_source_recency_importance_sort_key`,
  `_rank_source_stories`) + M3 residual-#2 seam (`cluster_importance_by_story` param threaded
  to `score_and_classify_for_user`); `source_roll_slots` clamped ≥0.
- `agents/pipeline/produce_caps.py` — SP3 `DEFAULT_HEADROOM_MULTIPLIER = 1.5` + default flip
  + docstring/doctest update (also fixed a pre-existing invalid `"markets"` doctest category
  to the valid `"business"`).
- `agents/pipeline/stages/ranking.py` — M3 residual #2: `cluster_importance_by_story`
  threaded through `score_stories_for_interest` / `generate_fallback_candidates` /
  `score_candidates_for_user` / `score_and_classify_for_user` to `compute_story_score`.
- `agents/pipeline/orchestrator.py` — `assemble_daily_feeds` accepts + forwards the shared
  `cluster_importance_by_story` map to `assemble_user_feed`.
- `agents/pipeline/daily_batch.py` — default `produce_cap_headroom` → 1.5; pass the (None)
  `cluster_importance_by_story` to `assemble_daily_feeds` with the M3 residual-#1 note.
- `tests/agents/pipeline/test_feed_assembly.py` — SP1/SP2/SP4 + M3 residual-#2 cases.
- `tests/agents/pipeline/test_produce_caps.py` — SP3 headroom (pass-rate reason) case;
  pinned the 4 cross-user-max tests at `headroom_multiplier=1.0` (isolate from the new default).
- `tests/agents/pipeline/test_ranking.py` — `TestClusterImportanceThreadsThroughScorer`.

### Phase DoD: PASS
`pytest tests/agents/pipeline/test_feed_assembly.py tests/agents/pipeline/test_produce_caps.py`
→ **36 passed**. Plus `test_ranking.py` + `test_fallback_tree.py` + importance + daily_batch:
all green. `ruff check` on all touched py (incl. tests): clean. `produce_caps.py`
`--doctest-modules`: passes.

### Slop / CSO self-scan: PASS
- No TODO/FIXME/print/console.log in touched files; ruff confirms no unused imports.
- Correctness: `guaranteed_cap` bounds `granted_total ≤ total_target`; final materialize caps
  at `feed_slot_budget`; over-budget 35→30 cap verified. Spill rule deterministic on ties
  (recency→importance→id) — exercised by `test_source_spill_importance_breaks_recency_ties_then_id`.
- Additive seam: empty/None `cluster_importance_by_story` is byte-identical to the pre-M3
  feed (asserted). M2 keyword DepthMatch +1 + M3 β=0.45 untouched.
- Boundary documented: a source category with NO budget row (user dialed it to 0) does not
  get guaranteed slots — consistent with the topic-category 0-budget semantics; the guarantee
  applies to budgeted source categories.

### LIVE-E2E residual (deferred, NOT run — offline sandbox)
A real batch producing a real `daily_feeds` that LEADS with real followed-source items
(real follows → real source stories → this allocator). Plus, once the online clusterer is
wired into `daily_batch` (M3 residual #1), the live cluster_importance map feeding the
assembly seam.

### Concerns flagged for M6a + M7
- **M6a** (source/cluster onboarding UI) is what POPULATES `user_content_sources` /
  `user_personalities` → the live `source_stories_by_user` this allocator consumes. The pure
  allocator is ready; M6a closes the live coupling (the LIVE-E2E residual). M6a must ensure a
  followed source category gets a budget row (or default), else its follows won't get
  guaranteed slots (the 0-budget boundary above).
- **M7** updates `reference/ranking-spec.md` + taxonomy/reuse docs. The ranking-spec already
  carries the revamp banner; M7 should also document the M6b source-first priority + spill
  rule + the 1.5 headroom (currently documented inline in code + this progress file).
- **M3 residual #1** remains: wiring the online clusterer into `daily_batch` (score_clusters +
  cluster_store.upsert + the cluster↔story_id bridge) so the live cluster_importance map is
  produced. The assembly seam is ready to receive it the moment that lands.
