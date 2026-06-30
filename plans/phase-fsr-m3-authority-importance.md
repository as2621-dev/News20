# Phase FSR-M3: Authority-weighted importance (E1)

**Milestone:** M3 — Authority-weighted importance (E1) (`plans/prd.md`)
**Status:** SHIPPED (2026-06-30) — β pinned at 0.45; see `plans/phase-fsr-m3-authority-importance-progress.md`
**Estimated effort:** M

## Goal

When this phase is done, a clustered story's `story_importance` is the shared-pool **E1** model — `W_breadth·breadth + W_authority·(authority+diversity) + W_velocity·velocity + W_recency·recency + W_entity·entity`, **normalized within category**, with syndication dampening — computed once per cluster from a config-driven source-tier authority table, replacing `produce_gate.compute_importance_score`'s raw `min(1, outlet_count/12)`; and `ranking.py`'s `IMPORTANCE_WEIGHT` (β) is raised so a genuinely big story outranks a well-matched minor one. This is the **existing** E1 model from `plans/shared-pool-rework-master-plan.md` / `reference/shared-pool-pipeline.md` §4 — implemented, not forked.

## Context the executor must not re-derive (Rule 8)

- The E1 formula is **already specified** in `reference/shared-pool-pipeline.md` §4. Implement that, do not author a second importance formula (PRD Decision #5, Rule 7).
- The cluster table already carries the destination fields: `StoryCluster` (`agents/pipeline/clustering/models.py`) and `0018_story_clusters.sql` already have `cluster_importance`, `cluster_velocity`, `cluster_outlet_count`, `cluster_member_count`, `cluster_category`, `cluster_first_seen_utc`, `cluster_last_seen_utc`. **0018 is applied to prod (2026-06-19).** No new migration is needed — and a new importance/velocity migration would be wrong (the columns exist). **No DB schema change in this phase.**
- **No source-tier authority table exists yet** (grep confirms it lives only in prose in `reference/shared-pool-pipeline.md`). It is built here as a **Python config artifact** (a domain→tier map + tier→weight), not a DB table — consistent with `produce_gate`'s "single config source, no scattered constants" convention.
- The recency half-life primitive already exists: `produce_gate.compute_freshness_score` (~24h half-life). E1's `recency` term **reuses** it (spec §4: "matches ranking-spec Freshness") — do not re-implement decay.
- The β term is `IMPORTANCE_WEIGHT = 0.3` in `agents/pipeline/stages/ranking.py` (single config constant, line ~62). The ranking importance term is computed at `ranking.py::compute_story_score` (line ~526) via `compute_importance_score(story.story_outlet_count)`.
- This is a **PURE-MATH** phase over story/cluster rows — fully offline-unit-testable. No live GDELT / DB / LLM. Mirror existing test structure (`tests/agents/pipeline/test_<module>.py`, e.g. `test_produce_gate.py`, `test_ranking.py`); mock nothing external because nothing external is touched.

## Sub-phases

### Sub-phase 1: Source-tier authority table (config + lookup)
- **Files touched:** `agents/pipeline/importance/__init__.py` (new package), `agents/pipeline/importance/source_tiers.py` (new), `tests/agents/pipeline/importance/test_source_tiers.py` (new)
- **What ships:** A config-driven `outlet_authority(domain) -> tier_weight` lookup: a static `SOURCE_TIER_BY_DOMAIN` map (high-authority, ideologically-varied outlets vs content farms) + a `TIER_WEIGHT` scale + a default tier for unknown domains, plus an `authority_and_diversity(outlet_domains) -> float` aggregator that rewards **varied high-authority** outlets over a pile of low-tier/content-farm reprints. Single config source (no scattered constants), mirroring `produce_gate`'s convention.
- **Definition of done:** Unit tests (no DB/network) assert: (a) a known high-authority domain returns a strictly higher tier weight than a known content-farm domain; (b) an unknown domain falls to the documented default tier (no crash); (c) `authority_and_diversity` over {3 distinct high-authority outlets} returns a strictly higher score than over {10 identical-tier content-farm outlets}, encoding the WHY — authority+diversity beats raw volume (Rule 9). Tests fail if the tier ordering or the diversity-over-volume property regresses.
- **Dependencies:** none. INDEPENDENT.

### Sub-phase 2: E1 `story_importance` computation (per-cluster, un-normalized terms)
- **Files touched:** `agents/pipeline/importance/story_importance.py` (new), `tests/agents/pipeline/importance/test_story_importance.py` (new)
- **What ships:** A pure `compute_story_importance_terms(cluster, member_outlets, now_utc) -> ImportanceTerms` that returns the five E1 component terms per `reference/shared-pool-pipeline.md` §4 — `breadth` = norm over **cluster-deduped distinct outlet count** (syndication dampening: distinct outlets, not raw reprint count, via `cluster_outlet_count`/distinct member outlets, NOT `cluster_member_count`), `authority` = sub-phase-1 `authority_and_diversity`, `velocity` = `cluster_velocity` input (coverage acceleration), `recency` = reuse `produce_gate.compute_freshness_score(cluster.cluster_last_seen_utc, now_utc)` clamped at 1.0, `entity` = entity-prominence over the cluster — plus a `combine(terms, weights) -> raw_score` that applies the `W_*` config weights. Weights are single-source config constants (`W_BREADTH/W_AUTHORITY/W_VELOCITY/W_RECENCY/W_ENTITY`).
- **Definition of done:** Unit tests on synthetic clusters assert: (a) **authority beats syndication burst** — an authority-varied 10-distinct-outlet cluster scores a strictly higher raw importance than a 20-outlet single-wire content-farm burst (the headline DoD from M3); (b) **syndication dampening** — adding N reprints from the *same already-counted* outlet does not raise the breadth term (distinct-outlet, not member-count); (c) **recency clamp** — a future-dated `cluster_last_seen_utc` clamps recency at 1.0 (no >1 inflation); (d) a missing/None `cluster_velocity` degrades gracefully to 0 contribution, not a crash. Each asserts the *reason*, not just "returns a float".
- **Dependencies:** Sub-phase 1 (uses `authority_and_diversity`). SEQUENTIAL after 1.

### Sub-phase 3: Within-category normalization + cluster wiring
- **Files touched:** `agents/pipeline/importance/story_importance.py` (extend), `tests/agents/pipeline/importance/test_story_importance.py` (extend)
- **What ships:** `normalize_importance_within_category(clusters_with_raw) -> {cluster_id: importance}` that min-max (or max-) normalizes each cluster's raw E1 score **within its `cluster_category`** so a big sport story competes with sport, not a war — and `score_clusters(clusters, member_outlets_by_cluster, now_utc) -> list[StoryCluster]` that computes raw terms (SP2), normalizes within category, and returns clusters with `cluster_importance` populated (a pure transform over `StoryCluster` objects — **no DB write**; the batch persists via the existing `cluster_store.upsert`). Replaces the role of `produce_gate.compute_importance_score`'s raw `min(1, outlet_count/12)` as the intrinsic importance source for clustered stories.
- **Definition of done:** Unit tests assert: (a) **within-category normalization** — two clusters in `sport` and two in `world_politics`, each category's top cluster reaches the category-max (≈1.0) independently, so the biggest sport story is not suppressed by a bigger war story; (b) **single-cluster / single-category edge** — a category with one cluster does not divide-by-zero and is **not** spuriously inflated to 1.0 (per PRD edge case — document and test the chosen rule, e.g. neutral mid-value or raw passthrough); (c) **empty category** — no clusters → empty result, no crash; (d) the returned `StoryCluster` objects carry a populated `cluster_importance` in `[0,1]`. Fails if normalization leaks across categories or the degenerate cases regress.
- **Dependencies:** Sub-phase 2. SEQUENTIAL after 2.

### Sub-phase 4: Raise β + route ranking importance through E1
- **Files touched:** `agents/pipeline/stages/ranking.py` (`IMPORTANCE_WEIGHT` constant + the `compute_story_score` importance read), `tests/agents/pipeline/test_ranking.py` (extend)
- **What ships:** (1) `IMPORTANCE_WEIGHT` (β) raised from `0.3` to a pinned value (default **0.45**; chosen so the big-story-beats-minor ordering flips — exact value confirmed by the DoD test, see Open questions) with α/β/γ kept as single config constants. (2) `compute_story_score` reads the story's **E1 `cluster_importance`** when available (threaded in via the candidate/story, defaulting to the existing `compute_importance_score(story.story_outlet_count)` for un-clustered stories) so the raised β lifts authority-weighted importance, not the raw outlet count. Surgical: the Score *shape* (§1), EntityBonus, DepthMatch, and allocation are untouched (PRD: "only the Importance definition + β change").
- **Definition of done:** A unit test on two fixture candidates encodes the bug fix (Rule 9): a **genuinely big story** (high E1 `cluster_importance`, modest affinity/depth) and a **well-matched minor story** (high affinity×depth, low E1 importance). At the **old** β=0.3 the minor story ranks first (reproduces the diagnosed bug); at the **new** β the big story ranks first. The test pins the threshold behaviour so β can't silently drift back. Existing `test_ranking.py` Score/EntityBonus/category tests still pass (no regression to the preserved Score shape).
- **Dependencies:** Sub-phase 3 (E1 importance is the value β now weights). SEQUENTIAL after 3.

## Phase-level definition of done

`pytest tests/agents/pipeline/importance/ tests/agents/pipeline/test_ranking.py tests/agents/pipeline/test_produce_gate.py` is green, and the suite proves the three M3 offline checks from the PRD: (a) an authority-varied 10-outlet story beats a 20-content-farm syndication burst; (b) importance normalizes within category; (c) at the raised β a genuinely big story outranks a well-matched minor one (a known minor-vs-major ordering flips vs the old β). No live GDELT/DB needed for any of it. The full existing `tests/agents/pipeline/` suite still passes (no regression to produce-gate, ranking Score shape, EntityBonus, or clustering).

## LIVE-E2E (deferred)

Importance-ordering sanity on a **real** day's clustered pool — confirming E1 ranks the day's genuinely big stories above minor well-matched ones end-to-end through `daily_batch` → `cluster_store` → assembly → `daily_feeds`. Requires GDELT egress + Supabase creds (absent in this sandbox). Marked deferred per PRD's Live-pipeline verification constraint; **not** claimed done from here (Rule 12).

## Out of scope

- News category-from-themes (M2), trusted-outlet fetch (M4), onboarding/clusters (M1/M5/M6), summaries (M7).
- The broader shared-pool rework beyond E1 (online-clusterer tuning M3b/M3c, reel formats, **MMR diversity**, τ-threshold tuning) — consumed as a dependency, not re-planned (PRD Out-of-scope; `plans/shared-pool-rework-master-plan.md`).
- Any DB migration. `0018` already provides `cluster_importance`/`cluster_velocity`/counts; no schema change.
- Wiring the source-tier *values* to a real, exhaustive outlet census — a seed map of representative high-authority + content-farm domains is enough for the offline DoD; full curation is a tuning/content-ops residual (flagged Open).
- Computing `cluster_velocity` itself (coverage-acceleration measurement) — E1 **consumes** the persisted `cluster_velocity` signal; producing it is the clusterer's job (ex-"breaking" velocity signal, owned by the shared-pool clusterer), not this phase.
- The MMR diversity term and the per-user E2 final-score reweight — E2 territory, not E1.

## Open questions

- **Exact β value.** Default 0.45; the SP4 DoD test pins whatever value flips the known minor-vs-major ordering. If the flip needs β so high it distorts the preserved Affinity-dominant intent (α=0.5), surface it rather than over-raising (Rule 12). *Resolvable inside `/run-phase` by the DoD test — not a planning gate (PRD Open items).*
- **E1 `W_*` term weights.** Spec says "start breadth-heavy; tune." Pin first-draft config constants whose ordering satisfies the SP2/SP3 DoDs; exact tuning is a residual.
- **Single-category normalization rule** (the degenerate "one story in a category" case): pick neutral-mid vs raw-passthrough and test it (SP3 DoD (b)); PRD flags this edge explicitly.
- **How `cluster_importance` reaches `compute_story_score`** (SP4): the cleanest seam is threading the cluster's E1 score onto the scored candidate/story at the point the batch already bridges `cluster_id ↔ story_id`. If the existing candidate plumbing makes that invasive (touching files beyond `ranking.py`), the executor should surface it and prefer the smallest seam (Rule 3) — falling back to the raw outlet count for un-clustered stories keeps the change additive.

## Cross-milestone dependencies (assumed)

- **`cluster_velocity` is produced upstream** by the shared-pool clusterer (the ex-"breaking" velocity signal, `CoverageMomentum`); E1 reads it. If it is not yet populated on real runs, E1's velocity term contributes 0 (graceful) and the offline DoDs still hold on synthetic clusters. Not a blocker for this phase.
- **Clusters exist** (M3a shipped; `0018` applied). E1 operates on `StoryCluster` rows. Offline tests use synthetic `StoryCluster` fixtures, so no live clustering is required.
- **No dependency on M1/M2/M4** source-tier *data*: the authority table here is a self-contained Python config seed (a real, exhaustive outlet census is a content-ops residual), so M3 does **not** block on a DB source-tier table from another milestone.

## Self-critique

**Product lens:** PASS. Traces to PRD User Stories 6, 7, 22, 23 and M3's three offline checks: SP2 DoD(a) = US22 (syndicated wire must not dominate), SP3 = US23 (normalized/defensible), SP4 = US6/US7 (the big story appears and outranks a minor one). No scope creep — theme-category (M2), fetch (M4), MMR/E2, and `cluster_velocity` production are explicitly out of scope. The riskiest *project* assumption (catalog quality) lives in M1, not here; M3's own riskiest bet — "authority+diversity reorders importance correctly" — is tested in the **first** sub-phase (SP1) and the headline reorder in SP2/SP4, not deferred. The 90-day metric (defensible importance ordering) becomes offline-measurable at phase end.

**Engineering lens:** PASS with one finding addressed. *Finding (P1):* an earlier framing risked a parallel importance formula — resolved by binding every sub-phase to `reference/shared-pool-pipeline.md` §4 and Decision #5 (implement E1, don't fork), and by reusing `compute_freshness_score` for recency rather than re-deriving decay. *Finding (P2):* an importance/velocity DB migration would collide with the already-applied `0018` and another concurrent planner's territory — resolved by making the authority table a **Python config artifact** and asserting **no schema change** (the columns already exist). Every DoD is fresh-context checkable (`pytest <path>` over pure functions; no "works end-to-end"). SP4 does lock in the β value, but does so *behind a DoD test that derives it* rather than guessing — the choices SP1–SP3 make (term definitions, normalization) stay flexible because SP4 only weights their output. No two sub-phases are secretly the same: SP1 = authority lookup, SP2 = raw terms, SP3 = normalization, SP4 = ranking integration — distinct files, distinct concerns.

**Risk lens:** PASS. *File-boundary conflicts:* SP2 and SP3 both touch `story_importance.py` + its test — marked SEQUENTIAL (3 depends on 2) so `/run-phase` orders them; no parallel write. SP1 is INDEPENDENT (own files). SP4 touches only `ranking.py` + its test. No file is written by two parallel sub-phases. *Cross-planner collision:* file name `phase-fsr-m3-*` and the new `agents/pipeline/importance/` package avoid the shared-pool `phase-m3a/m3b` files and the `clustering/` package; touches no shared plan/PRD/migration. *Test coverage:* every sub-phase DoD is a unit test that encodes the WHY and can fail on a business-logic change (Rule 9) — no "manual smoke". *Reversibility:* **no irreversible changes** — no migration, no public API, no data deletion; pure Python additions + two constant/seam edits in `ranking.py`. *Painting-into-a-corner:* simulate 1→2→3→4 — SP1 yields a tier lookup; SP2 consumes it for raw terms; SP3 normalizes those terms across clusters; SP4 weights the normalized output in ranking. SP4 still works given SP3's state (it reads `cluster_importance`, populated by SP3). Ordering is sound.

**Irreversible sub-phases:** none.
