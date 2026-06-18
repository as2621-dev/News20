# Execution Report — Phase M3a, Sub-phase 3: Cluster schema migration (AUTHOR ONLY)

**Status:** PASS (author-only — NOT applied to any DB, NOT committed)

## What it creates
`supabase/migrations/0018_story_clusters.sql` — the M3a Stage-C persistence layer (spec §3):
- `story_clusters` — one row per rolling cluster; `cluster_centroid vector(768)` (running-mean Gemini `text-embedding-004`, L2-normalized), `cluster_category feed_category not null` (reuses the existing enum), plus subcategory / reel_format / member_count / outlet_count / first+last_seen_utc / importance / velocity / status.
- `story_cluster_members` — member URLs that rolled into each centroid; composite PK `(cluster_id, member_url)`, FK to `story_clusters(cluster_id)` `on delete cascade`.
- `create extension if not exists vector;`
- Two indexes: ivfflat cosine (`story_clusters_centroid_idx`) + `(cluster_category, cluster_last_seen_utc)` window scan (`story_clusters_cat_lastseen_idx`).
- `-- pgvector fallback:` comment documenting the `real[]` + Python-cosine escape hatch (defaults to pgvector).
- Header documenting: M3a Stage-C persistence, running-mean Gemini 768-d centroid, does NOT touch `stories`/`story_url_aliases`, `cluster_id` bridges to `stories.story_id` via `story_url_aliases` in the batch (M3b), live apply DEFERRED to human checkpoint.

## Validation (STATIC ONLY — no DB connection made)
- **feed_category enum cite:** defined at `supabase/migrations/0008_feed_allocation.sql:57` (`create type feed_category as enum (...)`); value `podcasts` added at `supabase/migrations/0010_feed_category_podcasts.sql:29`. The new migration REUSES it (no redefinition).
- **Highest existing migration = 0017:** `ls supabase/migrations/` ends at `0017_drop_breaking_allocation.sql` (full list 0001–0017), so `0018` is the correct next number.
- **`grep -c "vector(768)" ...0018_story_clusters.sql` = 1** (confirmed).
- **`create table if not exists` count = 2** (both tables guarded).
- **`feed_category` referenced** in the migration (count = 2: comment + column).
- Idempotency: `create extension if not exists`, `create table if not exists`, `create index if not exists` throughout → re-run safe.

## Deferred-apply note
Live apply is DEFERRED to the human checkpoint (batched with 0017). Not applied, not committed.

### Apply command (human checkpoint only)
```
supabase db push --db-url "$SUPABASE_SESSION_POOLER_URL"
```
(IPv4 session pooler, port :6543)

### Verify query (after fresh apply — expected 0)
```
select count(*) from story_clusters;
```

## DoD (author-only) — PASS
File exists at the right path numbered 0018; `grep -c "vector(768)"` = 1; both `create table` statements present and guarded `if not exists`; references `feed_category` (reuses 0008 enum); NOT applied to any DB; apply command + smoke query recorded.
