# Phase M3a: Clustering foundations (embeddings, near-dup, schema, store)

**Milestone:** M3 — Global clustering engine + classification (`plans/shared-pool-rework-master-plan.md`). M3 is split into **M3a (foundations)** → M3b (assign-or-spawn engine) → M3c (ingest-to-target + classify + gap-fill).
**Status:** Not started
**Estimated effort:** L

## Goal
The reusable building blocks of the clusterer exist and are unit-tested in isolation — a Gemini embedding adapter (768-d, L2-normalized), a `datasketch` MinHash near-dup prefilter, the `0018_story_clusters.sql` schema (authored, not applied), and a cluster-store repository (load/upsert rolling centroids) — with **no live DB writes** and **no torch dependency**.

## Context for the executor
Implements the foundations for Stage (C) of `reference/shared-pool-pipeline.md` §2C/§3. Owner-approved deltas (see spec §3 banner): **Gemini embeddings (`text-embedding-004`, 768-d)** not local MiniLM; **migration is `0018`** (0017 used by M1); centroid is **`vector(768)`**; τ-tuning deferred to M6.
Verified codebase facts:
- `google-genai>=1.0` is ALREADY in `requirements.txt` — embeddings need NO new heavy package. Only `datasketch` is new.
- `agents/pipeline/llm_clients.py` has `LLMClient` with `_get_gemini_client()` (lazy `google.genai.Client`, key from `settings.gemini_api_key`) and `_retry_with_backoff(provider_name, call_fn)`. The embedding adapter REUSES this client + retry — do not create a second genai client.
- The new SDK embeds via `client.aio.models.embed_content(model="text-embedding-004", contents=[...])` (returns per-text embedding values). Confirm the exact response shape from the installed `google-genai` version and parse defensively.
- `feed_category` enum exists (7 values post-M1). `story_url_aliases` (0006) + `stories` exist — do NOT replace; the cluster bridges to them later (M3b/M3c).
- New code lives under a new package `agents/pipeline/clustering/` (keep each file < 500 lines per CLAUDE.md agent-code rule).

## Sub-phases

### Sub-phase 1: Gemini embedding adapter
- **Files touched:** `agents/pipeline/clustering/__init__.py` (new), `agents/pipeline/clustering/embeddings.py` (new), `tests/agents/pipeline/clustering/__init__.py` (new), `tests/agents/pipeline/clustering/test_embeddings.py` (new).
- **What ships:** `async def embed_texts(texts: list[str], *, llm_client: LLMClient, model: str = "text-embedding-004", batch_size: int = 100) -> list[list[float]]` — batches `texts`, calls `embed_content` via the injected `LLMClient`'s genai client wrapped in its `_retry_with_backoff`, returns one **L2-normalized 768-d** vector per input (order-preserving). A `cosine_similarity(a, b) -> float` helper (plain Python/`math`, since vectors are normalized → dot product). Structured logging (`embed_texts_started/completed` with counts, NOT the text). Empty input → `[]`.
- **Definition of done:** `tests/agents/pipeline/clustering/test_embeddings.py` (Gemini client MOCKED — no real API call) asserts: (a) N texts → N vectors each length 768 and L2-norm ≈ 1.0; (b) batching: 250 texts with batch_size 100 → 3 underlying calls; (c) `cosine_similarity` returns 1.0 for identical, ~0 for orthogonal. `pytest tests/agents/pipeline/clustering/test_embeddings.py -q` green; `ruff check` clean. NO network call in tests.
- **Dependencies:** none

### Sub-phase 2: MinHash near-duplicate prefilter
- **Files touched:** `agents/pipeline/clustering/near_dup.py` (new), `tests/agents/pipeline/clustering/test_near_dup.py` (new), `requirements.txt` (add `datasketch`).
- **What ships:** `group_near_duplicates(items: list[NearDupItem], *, threshold: float = 0.85, num_perm: int = 128) -> list[list[int]]` (returns index groups of near-identical reprints) and `drop_exact_reprints(items) -> list[int]` (keeps one representative index per group). Shingling = 4-gram word shingles over normalized `headline + " " + lead`. `NearDupItem` is a tiny typed shape (index + text) so it's pure/testable. Uses `datasketch.MinHash` + `MinHashLSH`. Structured logging of group counts.
- **Definition of done:** `tests/.../test_near_dup.py` asserts: (a) two headlines differing by one word group together at 0.85; (b) two genuinely different stories do NOT group; (c) `drop_exact_reprints` collapses a 3-reprint cluster to 1 representative and leaves distinct items untouched. `pytest tests/agents/pipeline/clustering/test_near_dup.py -q` green; `pip show datasketch` resolves (add to requirements with a version floor); `ruff check` clean.
- **Dependencies:** none

### Sub-phase 3: Cluster schema migration (`⚠ irreversible` — DB, AUTHOR ONLY)
- **Files touched:** `supabase/migrations/0018_story_clusters.sql` (new).
- **What ships:** the `story_clusters` + `story_cluster_members` schema from spec §3, with **`cluster_centroid vector(768)`**, `create extension if not exists vector;`, `cluster_category feed_category not null` (reuse the existing enum), the two indexes (ivfflat cosine + (category,last_seen)), and the FK + cascade on members. Use `create table if not exists` / `create index if not exists` so a re-run is safe. A `-- pgvector fallback` comment documents the `real[]`+Python-cosine path if the extension is unavailable (spec §3). Header documents that **live apply is DEFERRED** to the human checkpoint and that it does NOT touch `stories`/`story_url_aliases`.
- **Definition of done:** file exists at the right path numbered `0018`; `grep -c "vector(768)" supabase/migrations/0018_story_clusters.sql` = 1; both `create table` statements present and guarded `if not exists`; references `feed_category`; **NOT applied to any DB** (no `db push`/`psql`); a note records the apply command + a `select` smoke query for the human checkpoint.
- **Dependencies:** none (but its column shape must match SP4's repository)

### Sub-phase 4: Cluster-store repository (load/upsert rolling centroids)
- **Files touched:** `agents/pipeline/clustering/cluster_store.py` (new), `agents/pipeline/clustering/models.py` (new), `tests/agents/pipeline/clustering/test_cluster_store.py` (new).
- **What ships:** pydantic models `StoryCluster` + `ClusterMember` (mirror the 0018 columns; `cluster_centroid: list[float]`), and a repository with an injected supabase client: `load_active_clusters(client, *, category=None, since_utc) -> list[StoryCluster]` (reads rolling centroids within the time window), `upsert_cluster(client, cluster) -> None`, `add_cluster_members(client, cluster_id, members) -> None`, and centroid (de)serialization helpers (Python list ↔ the pgvector text form `"[...]"`). All DB access goes through the injected client (mocked in tests) — **no real connection**.
- **Definition of done:** `tests/.../test_cluster_store.py` (supabase client MOCKED) asserts: (a) `load_active_clusters` parses mocked rows into `StoryCluster` objects with a 768-len centroid and the correct window filter applied; (b) `upsert_cluster` calls `.upsert` on the `story_clusters` table with the serialized centroid; (c) centroid serialize→deserialize round-trips a 768-float vector exactly. `pytest tests/agents/pipeline/clustering/ -q` green; `ruff check` clean.
- **Dependencies:** Sub-phase 1 (768-d vector contract), Sub-phase 3 (column shape)

## Phase-level definition of done
`pytest tests/agents/pipeline/clustering/ -q` is fully green; `ruff check agents/pipeline/clustering/` clean; `datasketch` is in `requirements.txt`; `0018_story_clusters.sql` exists with `vector(768)` and is **NOT applied**; no torch / sentence-transformers dependency was added; the rest of the suite (`pytest tests/agents/pipeline/ -q`) remains green. These four modules are the foundation M3b's assign-or-spawn engine composes.

## Out of scope
- The assign-or-spawn loop, blocking, cross-day continuity — **M3b**.
- Stage B targeted ingest, centroid classification, gap-fill — **M3c**.
- Applying `0018` to the live DB — human checkpoint (batched with `0017`).
- E1 importance / MMR / reel formats — M4/M5.
- τ_assign / min_cluster_size real-corpus tuning — M6.

## Open questions
1. **Embedding model id:** `text-embedding-004` (768-d) vs `gemini-embedding-001` (MRL-truncatable). → recommend `text-embedding-004` (stable 768-d, no truncation logic). Confirm the installed `google-genai` supports it; if not, fall back to the available embedding model and keep 768-d (or adjust the migration dim to match — flag loudly if the dim must change).
2. **pgvector availability** on the Supabase instance — assumed enabled; the migration's `create extension if not exists vector;` handles it, and the `real[]` fallback is documented. Verified at the live-apply checkpoint, not now.

## Self-critique

**Product lens:** PASS. M3a is the load-bearing foundation for decision #3 (custom online clusterer) — the embedding + near-dup + persistence primitives the engine composes. No out-of-brief feature. The rework's riskiest assumption (τ_assign separating same/different events on short text) is correctly the M6 spike, not M3a; M3a deliberately ships tunable defaults so the engine can exist and be structurally validated first. Owner-approved deviations (Gemini API embeddings, deferred tuning) are recorded in the spec §3 banner, not silently blended (Rule 7).

**Engineering lens:** PASS. Every DoD is fresh-context checkable (pytest green on a named test dir, `grep -c "vector(768)"` = 1, `ruff` clean, `pip show datasketch`) — not "works end-to-end". All four sub-phases mock external boundaries (Gemini client, supabase client) per CLAUDE.md §6 — zero real API/DB calls in tests, zero cost. Stack-conformant: reuses the existing `LLMClient` + `google-genai` dep (no torch), adds only the tiny pure-Python `datasketch`. SP4 (the repository) doesn't lock a premature API — its load/upsert shape is the natural §3 contract M3b consumes. No two sub-phases are secretly the same: embeddings (vectorize), near-dup (MinHash), schema (SQL), store (persistence) are four distinct concerns.

**Risk lens:** PASS with flags. **File boundary:** SP1/SP2/SP3 touch disjoint new files; SP4 adds two new files + a test. SP1 and SP4 both conceptually involve the 768-d contract but touch different files (embeddings.py vs models.py/cluster_store.py) — marked as a dependency, not an overlap. No two sub-phases write the same file. **Reversibility:** SP3 is `⚠ irreversible` (DB schema) — fully mitigated: AUTHOR ONLY, `create ... if not exists` guards, live apply deferred to the human checkpoint, touches no existing table. **Test coverage:** every sub-phase DoD includes mocked unit tests asserting *why* (L2-norm=1, batching call-count, near-dup groups/separates, centroid round-trip) per Rule 9. **Painting-into-a-corner:** 1(embed)→2(near-dup)→3(schema)→4(store) simulated — SP4 needs the 768-d contract (SP1) and the column shape (SP3), both present before it runs. No corner. **Dependency-addition CSO note for /run-phase:** `datasketch` is the only new package — verify it's maintained + not a typosquat at the phase-level CSO pass.

**Irreversible sub-phases:** Sub-phase 3 (`⚠ irreversible` — `0018_story_clusters.sql` schema; mitigated: author-only, `if not exists` guards, live apply deferred).
