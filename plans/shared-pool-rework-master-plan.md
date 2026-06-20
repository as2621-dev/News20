# Master Plan — Shared-Pool Ingestion, Clustering & Reranking Rework

**Date:** 2026-06-18
**Scope:** A focused architecture rework *within* the News20 product — NOT a new product. The global product north star stays `plans/master-plan.md`; this plan governs the ingestion → clustering → ranking inversion only.
**Status:** Active
**Source:** Owner brief (2026-06-18) + verified deep-research report (see `reference/shared-pool-pipeline.md` §0 for citations).

---

## Vision (one paragraph)

Stop hunting news per-user. Run News20's daily batch like a **newsroom**: size total demand across all users, fill **one shared pool** of stories, **deduplicate raw articles into "stories" (clusters) once globally**, label and rank each story **once** for intrinsic importance, then draw a **personalized 30** per user off that shared, ranked pool with MMR diversity. This kills duplicate-ish reels, ends redundant GDELT querying, retires the "breaking" *category* (its value survives as a velocity *signal*), and lets followed entities produce the **format** a user expects (FIFA → one match-results digest, not eight match reels).

## Tech stack (unchanged — this is a rework, not a re-platform)

- **Pipeline:** Python 3.12, `agents/ingestion` + `agents/pipeline`, daily batch (`agents/pipeline/daily_batch.py`). Rationale: the whole pipeline already lives here; we extend it.
- **Data:** Supabase (Postgres). Rationale: stories, feeds, entities, `story_url_aliases` already here; we add cluster tables alongside.
- **Ingestion source:** GDELT BigQuery GKG (project `blip-498623`) + followed YouTube/X. Unchanged.
- **New libraries (ADOPT, not build):** `sentence-transformers` (`all-MiniLM-L6-v2`, 384-d) for embeddings; `datasketch` (MinHash LSH) for near-dup. Rationale: both are standard, library-backed, MIT/Apache.
- **Build (small, custom):** the online assign-or-spawn clusterer (~200 lines) and the MMR re-ranker (~30 lines). Rationale: only custom path gives cross-day story continuity (ties into `story_url_aliases`); BERTopic is batch-only and was ruled out.
- **Agents:** none added. Cluster classification + podcast key-ideas use the existing Gemini LLM clients. Transcripts reuse the existing YouTube-adapter yt-dlp path (TLDW lineage).
- **Jobs/Hosting:** unchanged (existing daily batch / Railway worker).

## Architecture (data flow)

```
                         ┌─────────────────────────────────────────────┐
                         │            DAILY BATCH (per run)             │
                         └─────────────────────────────────────────────┘

  active users ──► (A) DEMAND          pool_target[category][subcat]
  + allocations     aggregate allocator demand:  max_over_users × buffer, floor
                         │
                         ▼
  GDELT GKG ───►  (B) INGEST-TO-TARGET ──► raw article candidates
  followed YT/X        broad slice + source items
                         │
                         ▼
                  (C) CLUSTER ONCE GLOBALLY  ── MinHash LSH near-dup prefilter
                      MiniLM embed → time-window block → assign-or-spawn
                      rolling centroids persisted ► story_clusters (+ story_url_aliases)
                         │
                         ▼
                  (D) CLASSIFY centroid ► ONE primary (category, subcat); fallback for singletons
                         │
                         ▼
                  (E1) INTRINSIC RANK (shared, once/story)  story_importance
                       breadth · authority+diversity · velocity · recency · entity prominence
                       normalized WITHIN category
                         │
                  (E1.5) REEL FORMAT  EVENT │ DIGEST(sport,markets) │ UPDATE(persisted) │ SOURCE(YT/X)
                         │
                         ▼  ◄── gap-fill loop (bounded: ≤3 rounds, widen 24→48→72h, else fail loud)
                  (E2) PER-USER RE-RANK @ assembly (existing allocator + new terms)
                       final = w1·importance + w2·relevance − w3·MMR_diversity − w4·already_seen
                         │
                         ▼
                  produce reels (per format) ──► daily_feeds (unchanged read contract)
```

## Key design decisions

1. **Kill the "breaking" category; keep velocity as a signal.** Remove the `breaking` tier/enum/template/allocator-pass everywhere (see `reference/shared-pool-pipeline.md` §1 for the exact 11 sites). Coverage velocity/acceleration becomes a term in `story_importance` (E1). Rules out: a standalone breaking bucket, the `_select_breaking` pass, `DEFAULT_BREAKING_SLOTS`, `breaking_headroom`. **7 feed categories remain** (5 topic + 2 source).

2. **Invert ingestion to a demand-sized shared pool (A→E).** Candidate generation moves from per-user fallback-tree search to one shared pool sized by aggregate demand. Rules out per-user GDELT querying. Supersedes the candidate-generation half of `ranking-spec.md` §2 (the scoring/allocation half stays).

3. **Custom online assign-or-spawn clusterer; NOT BERTopic.** Persistent rolling centroids in Supabase give cross-day continuity for free. Rules out batch re-clustering and per-day-only topic models. (Verified prior art: nonparametric streaming K-means, EACL 2021; USTORY SIGIR'23.)

4. **Classify the cluster centroid into exactly ONE primary (category, subcategory).** One LLM call per story, not per article. Singleton/noise clusters get a fallback classifier, never dropped. Rules out per-article voting as the default (kept as a fallback option to benchmark).

5. **Two-layer ranking (PP-Rec design, not its neural model).** Intrinsic `story_importance` computed once and shared; per-user relevance + MMR diversity applied at assembly. Rules out a trained CTR model (no engagement data yet) and ID-based collaborative filtering (news cold-start).

6. **MMR is the anti-duplicate-feed mechanism.** λ-tunable relevance/novelty at the per-user re-rank. Rules out ad-hoc "don't show two similar reels" heuristics.

7. **Reel *format* follows the follow.** EVENT (pick biggest) for world/tech/culture; **DIGEST/roundup** for sport + markets (group many small clusters into one reel); **UPDATE** auto-detected for persisted stories that advanced; **SOURCE** (creator-attributed) for YT/X. Rules out one-event-one-reel uniformity.

8. **"Seen once" reuses existing machinery — no new tracking.** Suppress any story already in a user's prior `daily_feeds` (existing `prior_feed_story_ids`). Persistent EVENT clusters keep one id (seen once); DIGESTS get a fresh id per day (recur naturally). Rules out a `development_version` counter and an impressions table (explicitly rejected by owner as over-complex). Accepted trade-off: a long-running event already seen won't get a separate push-update reel; big developments surface as new sub-event clusters anyway.

9. **Gap-fill loop is bounded.** If a cell is under target: ≤3 rounds, widen window 24h→48h→72h, then **fail loud and under-fill**. Rules out unbounded "search until N" loops.

## Milestones (coarse — `/plan-phases` slices these)

- **M1 — Kill breaking + taxonomy cleanup.** Remove the breaking category from all 11 sites (Py + TS + SQL enum/migration + templates + sim). 7 categories. Lowest risk; unblocks everything. *Done when:* batch + app run with no `breaking` references and tests green.
- **M2 — Demand computation + pool sizing.** Aggregate per-user allocator demand → `pool_target[cat][subcat] = max_over_users × buffer`, with floor. *Done when:* a run emits a correct shopping list for the active user set.
- **M3 — Global clustering engine + classification (the core).** MinHash LSH prefilter + MiniLM embeddings + time-window blocking + assign-or-spawn; persist `story_clusters` rolling centroids; classify centroid → one (category, subcat). *Done when:* a day's articles collapse into deduped stories with stable cross-day ids, validated on ash's corpus.
- **M4 — Intrinsic importance + per-user MMR re-rank.** `story_importance` (E1) + MMR diversity term folded into the existing assembly Score. *Done when:* feed shows no near-duplicate reels and importance ordering is sane.
- **M5 — Reel formats.** DIGEST roundup (sport/markets), UPDATE detection, SOURCE treatment (YT "X said" / podcast key-ideas via TLDW transcripts / X tweet-clustering). *Done when:* FIFA follow yields one results-digest reel; a followed channel yields a "key ideas" reel.
- **M6 — Offline tuning + cutover.** Tune cosine threshold + `min_cluster_size` on News20's own GDELT headline+lead corpus; benchmark centroid-classify vs article-vote; cut the daily batch over to the shared-pool path. *Done when:* tuning report exists and the live batch runs end-to-end on the new path.

## Riskiest assumption and how we test it

**Assumption:** off-the-shelf `all-MiniLM-L6-v2` + a tuned cosine threshold cleanly separates same-event vs different-event on GDELT **headline+lead** text (short, not full-article) without over-merging (two events glued) or over-splitting (one event as N stories). Library defaults are tuned for full articles, not ours.
**Test:** a small **offline tuning spike early in M3** — hand-label same/different on a few hundred ash-corpus pairs, sweep threshold + `min_cluster_size`, measure over-merge vs over-split. This gates M3; if generic embeddings underperform, benchmark a stronger off-the-shelf encoder *before* considering entity-aware fine-tuning (2024+ generic embeddings largely close that gap — don't over-invest).

## Out of scope

- Trained/ML ranking (PP-Rec neural model, learned CTR) — we copy the *design*, not the model. No engagement data yet.
- Entity-aware embedding fine-tuning — benchmark off-the-shelf first.
- Real-time/streaming clustering — stays a **daily batch**; "online" here means assign-or-spawn within the batch, not a live stream.
- Changes to reel rendering, audio/karaoke, or the client `daily_feeds` read contract.
- `news-please` adoption as a crawler (its GDELT path is the Events-DB Solr crawler, not our BigQuery GKG path) — keep our GDELT adapter.

## Phases

### M1 — Kill breaking + taxonomy cleanup
- [Phase SP1](phase-sp1-kill-breaking.md) — remove the breaking category from Python + TS + DB + sims; keep the velocity signal; 7 categories, feed still totals 30. **SHIPPED (12380e2)**; migration 0017 authored, live apply deferred.
- [Phase SP2](phase-sp2-feed-rebuild-safety.md) — make the source-reel feed rebuild non-destructive (the "Nitish-eviction" bug): preserve existing source rows, fail-safe write, non-clobbering backup, source-aware `allocate_test_feeds`. Prereq for safe taxonomy reassembly.
- [Phase SP3](phase-sp3-taxonomy-unification.md) — **SUPERSEDES the 5-category fold**: unify onboarding + Build-your-30 + reel chip on the **8 picker roots** (`ai, geopolitics, business, environment, politics, tech, sport, arts`) + youtube/x; `0020` enum + `segment_slug` reconcile + backfill; finish breaking removal in the 3 lagging scripts. (Owner decision 2026-06-18; see `~/.claude/plans/a-few-things-to-bright-lamport.md`.)
- [Phase SP4](phase-sp4-build-your-30-gate.md) — Build-your-30 shows ONLY backed categories (close the no-signal + stale-saved gates) and locks selected-order == feed-order with a test.

*(Phases P3–P6 of the owner plan — demand→ingest wiring, online clusterer + cross-category single-assignment, importance + final-pass dedup + distribution, source-reel YT/X refinements — map to M3b/M3c/M4/M5 below and are sliced on a later `/plan-phases` pass once the SP3 taxonomy lands and M3 thresholds are tuned.)*

### M2 — Demand computation + pool sizing
- [Phase M2](phase-m2-demand-pool-sizing.md) — aggregate per-user allocator demand → subcategory-granular `pool_target = ceil(max_over_users × BUFFER)`, floored; emit the shopping list (additive, no migration). **SHIPPED (b176dc3)**.

### M3 — Global clustering engine + classification (split into M3a → M3b → M3c)
- [Phase M3a](phase-m3a-clustering-foundations.md) — embeddings (Gemini 768-d) + `datasketch` near-dup + `0018_story_clusters.sql` + cluster-store repo. Foundations, mocked tests, no torch. **SHIPPED (9018877); 0018 applied to prod 2026-06-19.**
- [Phase M3b](phase-m3b-online-clusterer.md) — the online assign-or-spawn engine: time-window blocking + cosine assign/spawn + running-mean centroids + cross-day continuity via `story_url_aliases`. Composes M3a; no new deps, no migration (0018 live). **Sliced 2026-06-19.**
- M3c (generated after M3b) — Stage B ingest-to-target (consume `pool_target`) + Stage D centroid classification + bounded gap-fill; wire B→C→D behind a flag.

*(Decisions locked 2026-06-18: Gemini embedding API not local MiniLM; τ-tuning deferred to M6. M4–M6 generated on demand.)*

## Open questions for `/plan-phases`

1. **Where does the embedding model run?** Local `sentence-transformers` inside the batch vs an embedding API — Railway worker memory/cold-start for MiniLM needs checking.
2. **Exact thresholds** (cosine, `min_cluster_size`, MinHash Jaccard for our text) — output of the M3 tuning spike, not assumable from defaults.
3. **Centroid-classify vs article-vote** for category assignment, and how each handles the HDBSCAN-style noise/singleton case.
4. **DIGEST grouping key** — confirm "competition/sector + day" is the right roll-up key for sport/markets, and where subcategories come from (interest taxonomy vs cluster classification).
5. **Pool buffer value** (1.5 vs 2.0) and the category-coverage floor magnitude — tune once multi-user demand is real (today ash dominates).
```
