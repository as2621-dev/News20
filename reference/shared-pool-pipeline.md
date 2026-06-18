# Reference — Shared-Pool Pipeline (Ingestion → Clustering → Reranking)

**Why this doc exists:** the single load-bearing reference for the demand-sized shared-pool rework — the A→E pipeline, the new Supabase schema, the scoring formulas, the reel formats, and the reuse-vs-build verdicts. `/plan-phases` and `/run-phase` read THIS file for the rework.
**Update when:** any stage contract, formula weight, schema column, threshold, or reuse decision changes.
**Companions:** `plans/shared-pool-rework-master-plan.md` (the plan), `reference/ranking-spec.md` (the per-user Score this extends — see §6), `reference/supabase-schema.md` (existing tables), `reference/archetypes.md` (UNRELATED — "archetype" there = source-recommendation persona; our reel shapes are called **reel formats**, see §5).

---

## 0. Proven prior art (deep-research, all claims verified 3-0 / 2-1)

| Decision | Backing | Source |
|---|---|---|
| Two-stage recall→rank cascade (shared pool → per-user rank → top-K) | production standard | ACM TOIS 2023 (10.1145/3530257) |
| Online nonparametric streaming K-means: assign-to-existing or spawn-new | the cluster-once paradigm | EACL/NAACL 2021 (arXiv:2101.11059); USTORY SIGIR'23 (arXiv:2304.04099) |
| Hybrid sparse (lexical/entity) + dense (embedding) reps beat either alone | clustering quality | arXiv:2101.11059 |
| Fuse time into the representation (+15% on News2013 TDT) | time is a top signal | LREC-COLING 2024 (aclanthology 2024.lrec-main.1416) |
| Intrinsic popularity score + personalized match score (copy design, not neural model) | two-layer ranking | PP-Rec, ACL 2021 (arXiv:2106.01300) |
| MMR for diversity / anti-redundancy on news | per-user re-rank | Carbonell & Goldstein, SIGIR'98 |
| Content-based, not ID-based (news cold-start, short life cycle) | embeddings mandatory | ACM TOIS 2023 |
| ADOPT: `sentence-transformers` (all-MiniLM-L6-v2, 384-d), `datasketch` (MinHash LSH) | standard, library-backed | sbert.net; BERTopic docs |
| Pattern reference only (don't depend): `chronicle` repo (MinHash LSH 0.85/128 + MiniLM + HDBSCAN) | hobby v0.1.0 | github.com/dukeblue1994-glitch/chronicle |
| `news-please` = per-article extractor only; its GDELT path is Events-DB Solr crawler, NOT our BigQuery GKG | don't use for GDELT | github.com/fhamborg/news-please |

**Gaps research could NOT answer (we design from first principles):** pool sizing (max vs sum), gap-fill loop bound, centroid-classify vs article-vote, exact thresholds for headline+lead text. See §2A, §2C, §7.

---

## 1. Removing "breaking" — the exact sites (M1)

Breaking is a *tier/enum/template*, NOT a seeded interest (`supabase/seed/interests.sql` has no breaking row). Remove from:

| # | File | Site |
|---|---|---|
| 1 | `agents/pipeline/categories.py:40,51,112,163` | `FeedCategory` literal, `DEFAULT_FEED_ALLOCATION["breaking"]=2`, `empty_category_buckets` |
| 2 | `agents/pipeline/feed_assembly.py:92,100,269,466,568-580,673-682` | `DEFAULT_BREAKING_SLOTS`, `SLOT_KIND_BREAKING`, `_select_breaking`, Pass-1 fill, ordering |
| 3 | `agents/pipeline/produce_caps.py:45,48,106-107,197-208` | `_BREAKING_CATEGORY`, `breaking_headroom` return + union |
| 4 | `agents/pipeline/daily_batch.py:661,670` | `caps, breaking_headroom = ...` call sites |
| 5 | `agents/pipeline/detail_templates.py:54,111,162,192,221` | `DetailCategory`, `DETAIL_TEMPLATES["breaking"]`, `_FEED_CATEGORY_TO_DETAIL`, `detail_category_for*` |
| 6 | `src/lib/feedBuckets.ts:70,93,127,172` | `DESIGN_BUCKETS.breaking`, `DESIGN_BUCKET_TO_ENUM`, default-allocation segment, `ALWAYS_INCLUDED_CATEGORY_BUCKET` |
| 7 | `src/lib/detailTemplates.ts` | TS twin of `DETAIL_TEMPLATES` |
| 8 | `src/types/feed.ts:181` | `feed_slot_kind` union |
| 9 | `supabase/migrations/0008_feed_allocation.sql:57` | `feed_category` enum value (NEW migration to drop/replace — Postgres enums need `ALTER TYPE`; plan in M1) |
| 10 | `agents/pipeline/sim/world.py:347`, `sim/ranking_sim.py` | sim fixtures + breaking-tier assertions |
| 11 | DB data | `user_feed_allocation` rows with `allocation_category='breaking'`; `stories.story_is_breaking` (0015) — keep column or repurpose as velocity flag (decide in M1) |

**KEEP (not a category — leave alone):** `agents/ingestion/adapters/gdelt_bigquery.py:115` ("breaking" is a recall keyword); the `CoverageMomentum = breaking|developing|settled` signal in `models.py:95` / `coverage_gdelt.py` — **this becomes the velocity input to `story_importance`** (E1), do not delete it.

**Result:** 7 feed categories — 5 topic (`world_politics, tech_science, markets, sport, culture`) + 2 source (`youtube, x`).

---

## 2. The A→E pipeline (stage contracts)

### (A) Demand computation
Run the existing allocator inputs for **every active user**, aggregate desired counts per `(category, subcategory)`:
```
pool_target[cat][sub] = ceil( max_over_active_users( demand[user][cat][sub] ) × BUFFER )   # BUFFER ∈ [1.5, 2.0]
pool_target[cat][sub] = max( pool_target[cat][sub], CATEGORY_FLOOR[cat] )                   # never starve a live category
```
**Why max, not sum:** two users wanting 10 geopolitics want *different* 10s; max×buffer serves the heaviest user and gives assembly room to differentiate. Sum over-fetches stories nobody ranks high enough to surface. (First-principles — no source addressed this; revisit when multi-user demand is real, today ash dominates.)
Reuse: `produce_caps.compute_category_produce_caps` already computes `cap = max slot_count any active user wants` — extend it to subcategory granularity + buffer + floor.

### (B) Ingest-to-target
Broad GDELT GKG slice (existing `GdeltBigQueryAdapter`) + followed YT/X (`source_pipeline.run_source_ingestion`). Pull enough raw `CandidateStory` objects to cover `pool_target` with headroom. No per-user querying.

### (C) Cluster once globally — the custom assign-or-spawn engine
New module (~200 lines), e.g. `agents/pipeline/clustering/online_clusterer.py`:
1. **Near-dup prefilter** — `datasketch` MinHashLSH over 4-gram word shingles of (headline+lead); drop exact reprints before embedding. Start threshold 0.85 Jaccard, 128 perms (tune in M6).
2. **Embed** — `all-MiniLM-L6-v2` on `headline + lead` → 384-d vector.
3. **Block** — candidate clusters sharing ≥1 dominant entity OR theme within a rolling **time window** (default 48h). Avoids O(n²).
4. **Assign-or-spawn** — cosine(article, cluster centroid) within block: `≥ τ_assign` → join (update centroid = running mean); else **spawn** a new cluster. `τ_assign` tuned in M6 (NOT a library default — those are full-article).
5. **Persist rolling centroids** to `story_clusters` (§3). A cluster seen yesterday is matched today → **cross-day continuity**; bridge to existing `story_url_aliases` so the story id is stable (reuse `daily_batch.build_story_id_resolver`).
Time-aware note: optionally fuse a recency feature into the vector (LREC-COLING 2024, +15%) — defer unless tuning shows over-merge across days.

### (D) Classify centroid → ONE primary (category, subcategory)
One LLM call (existing Gemini client) on the **cluster centroid** (canonical headline + top members), not per article. Maps to one of the 5 topic categories + a subcategory (from the interest taxonomy). Singleton/noise clusters → fallback classifier (cheap heuristic on dominant entity/theme), never dropped. (Centroid-classify vs article-vote: benchmark in M6.)

### (E1) Intrinsic importance — see §4. (E1.5) Reel format — see §5. (E2) Per-user re-rank — see §4 + §6.

### Gap-fill loop (bounded)
After (C)+(D), count unique stories per cell:
```
for round in 1..3:
    short = [cell for cell in pool_target if unique_count(cell) < pool_target[cell]]
    if not short: break
    window = [24h, 48h, 72h][round-1]
    re-ingest (B) for `short` cells only, widening to `window`; re-cluster (C); re-classify (D)
else:
    log.warn("pool_underfilled", cells=short, fix_suggestion="widen sources or lower target")  # FAIL LOUD, under-fill
```
Never loop unbounded. Targeted re-ingest hits only short cells, not everything.

---

## 3. New Supabase schema (migration `0017_story_clusters.sql`)

Highest existing migration is `0016`. Add alongside existing `stories` / `story_url_aliases` (do NOT replace them).

```sql
-- A persistent story cluster (rolling across days). One row per real-world story.
create table story_clusters (
  cluster_id            text primary key,                    -- stable id; bridges to stories.story_id via story_url_aliases
  cluster_centroid      vector(384),                         -- pgvector; running-mean MiniLM centroid
  cluster_category      feed_category not null,              -- the ONE primary category (D); 7-value enum post-M1
  cluster_subcategory   text,                                -- interest-taxonomy subcategory
  cluster_reel_format   text not null default 'event',       -- event | digest | update | source  (§5)
  cluster_member_count  int  not null default 1,             -- # articles (coverage breadth input)
  cluster_outlet_count  int  not null default 1,             -- # DISTINCT outlets (breadth signal)
  cluster_first_seen_utc timestamptz not null,
  cluster_last_seen_utc  timestamptz not null,               -- velocity/recency input
  cluster_importance    real,                                -- E1 score, normalized within category
  cluster_velocity      real,                                -- coverage acceleration (ex-"breaking" signal)
  cluster_status        text not null default 'active'       -- active | settled
);
create index on story_clusters using ivfflat (cluster_centroid vector_cosine_ops);
create index on story_clusters (cluster_category, cluster_last_seen_utc);

-- Membership: which articles/candidate stories belong to a cluster (for breadth + members).
create table story_cluster_members (
  cluster_id   text not null references story_clusters(cluster_id) on delete cascade,
  member_url   text not null,
  member_outlet text,
  member_seen_utc timestamptz not null,
  primary key (cluster_id, member_url)
);
```
**Reuse, don't rebuild:** `story_url_aliases` (0006) still maps normalized URL → stable story id; `stories` still holds the produced reel/digest; `daily_feeds` still holds the per-user feed (and is the "seen once" source via `prior_feed_story_ids`). `cluster_id` ↔ `story_id` bridged in the batch. **No** `development_version`, **no** impressions table (decision #8).

`pgvector` must be enabled (`create extension if not exists vector;`). If unavailable, store centroid as `real[]` and compute cosine in Python (small daily volume tolerates it).

---

## 4. Scoring formulas

### E1 — Intrinsic importance (shared, computed once per cluster)
```
story_importance(cluster) =
      W_breadth   · norm(cluster_outlet_count)          # # distinct outlets — strongest "real story" signal
    + W_authority · outlet_authority_and_diversity       # high-authority AND ideologically varied > 20 content farms
    + W_velocity  · norm(cluster_velocity)               # coverage acceleration — where dead "breaking" goes
    + W_recency   · exp_decay(cluster_last_seen_utc)      # ~24h half-life (matches ranking-spec Freshness)
    + W_entity    · entity_prominence(cluster)            # involves registry entities
```
Normalize **within category** (a big sport story competes with sport, not with a war). Start weights breadth-heavy; tune. This **enriches** the single `Importance` term in `ranking-spec.md §1` (which was just normalized `story_outlet_count`).

### E2 — Per-user final score (at assembly)
```
final(user, story) =   w1 · story_importance(story)                      # E1, shared
                     + w2 · relevance(user, story)                       # interest match + EntityBonus + source boost + subcat pref
                     − w3 · mmr_penalty(story | already_selected[user])  # diversity (§ below)
                     − w4 · already_seen(user, story)                    # 1 if story_id ∈ prior daily_feeds, else 0  → suppress
```
`relevance` = the existing `ranking-spec.md §1` Affinity×DepthMatch + §3a.1 EntityBonus + followed-source boost + subcategory preference. `already_seen` reuses `prior_feed_story_ids` (decision #8) — no new state.

### MMR diversity (the anti-duplicate-feed mechanism)
Greedy/incremental selection per user (Carbonell & Goldstein 1998):
```
next = argmax_{s ∉ S} [ λ · rel(user, s) − (1−λ) · max_{s'∈S} sim(s, s') ]
```
`sim` = cosine on cluster centroids (already have them). `S` = stories already chosen for this user's feed. `λ` tunable (1 = pure relevance, 0 = max diversity); start ~0.7. This is what stops 10 near-identical geopolitics reels.

---

## 5. Reel formats (NOT "archetypes" — see header)

The cluster's `cluster_reel_format` decides how it's grouped and rendered. Assigned by category + whether a followed entity drives it (D + E1.5):

| Format | Categories / trigger | Grouping | Rendering |
|---|---|---|---|
| **EVENT** | world_politics, tech_science, culture (default) | one cluster = one reel; pick the biggest by importance | standard reel |
| **DIGEST** (roundup) | **sport + markets** | **roll up** many small clusters into ONE reel, keyed by `competition/sector + day` | "Yesterday's results: …" / "Market wrap: …" |
| **UPDATE** | any persisted cluster that materially advanced (cross-day) | same cluster id, new development | "Catch-up: … Today: …" one self-contained reel (decision #8 — one reel, not two) |
| **SOURCE** (creator-attributed) | youtube, x (followed) | per video, or cluster a channel/account's items | see below |

**SOURCE — YouTube:** transcript via existing yt-dlp path (`youtube.py`, TLDW lineage) → "**[Channel] said: [key claims]**." If interview/podcast → extract **key ideas discussed** ("On [Podcast], [guest] argued X, predicted Y, pushed back on Z"), not a mechanics summary. Always attributed; never folded into the anonymous world pool. Flows through existing **source slots** (`feed_assembly._fill_source_slots`).

**SOURCE — X:** **cluster multiple important tweets** from a followed account (by topic/thread) into one reel ("This week [account] has been arguing X, citing Y"). Pick *important* tweets (engagement/substance); one reel, not one-per-tweet.

---

## 6. Relationship to `ranking-spec.md` (Rule 7 — conflict surfaced)

`ranking-spec.md` stays the source-of-truth for the **per-user Score** (Affinity×DepthMatch, EntityBonus §3a.1, category-budget allocation §3a.2). This rework changes the layers *around* it:
- **Supersedes** the candidate-generation half of `ranking-spec.md §2` (per-user fallback-tree search → shared pool, A→C).
- **Enriches** the `Importance` term of `ranking-spec.md §1` into the full E1 `story_importance`.
- **Adds** the MMR diversity term (E2) and the reel-format layer (§5).
- **Retires** the breaking tier (`ranking-spec.md §3.1 / §3a.2 pass 1`) → velocity signal.
A one-line banner is appended to `ranking-spec.md` pointing here for the rework deltas. Everything else in `ranking-spec.md` is preserved.

---

## 7. Reuse vs build

| Need | Verdict | What |
|---|---|---|
| Article extraction | reuse existing | our GDELT GKG adapter + YouTube/X adapters (NOT news-please for GDELT) |
| Embeddings | **adopt** | `sentence-transformers` all-MiniLM-L6-v2 (384-d) |
| Near-dup | **adopt** | `datasketch` MinHash LSH |
| Clustering engine | **build (~200 lines)** | online assign-or-spawn (§2C) |
| MMR re-rank | **build (~30 lines)** | §4 |
| Centroid classification | reuse existing | Gemini LLM client |
| Transcripts (YT/podcast) | reuse existing | yt-dlp path in `youtube.py` (TLDW lineage) |
| Cross-day id continuity | reuse existing | `story_url_aliases` + `build_story_id_resolver` |
| "Seen once" | reuse existing | `prior_feed_story_ids` |
| `chronicle` repo | pattern reference only | don't depend (hobby v0.1.0) |
| BERTopic | rejected | batch-only, no cross-day continuity |

## 8. Open tuning items (flag, not blockers)
- `τ_assign` cosine threshold + `min_cluster_size` for **headline+lead** text — offline experiment on ash's GDELT corpus (M3 spike / M6). Library defaults are full-article, not ours.
- centroid-classify vs article-vote (M6 benchmark).
- off-the-shelf encoder vs entity-aware fine-tune — benchmark generic first; 2024+ generic embeddings largely close the gap, don't over-invest in fine-tuning.
- BUFFER (1.5 vs 2.0) + CATEGORY_FLOOR — tune when multi-user demand is real.
