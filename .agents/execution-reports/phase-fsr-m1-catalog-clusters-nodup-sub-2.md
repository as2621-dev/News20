# Sub-phase 2 execution report — Pure cluster + no-dup resolver

**Phase:** phase-fsr-m1-catalog-clusters-nodup · **Sub-phase:** 2 · **Branch:** claude/feed-source-revamp-plan-388edf
**Status:** SUCCESS (NOT committed — orchestrator commits)

## Files touched (created)
- `agents/catalog/__init__.py`
- `agents/catalog/models.py`
- `agents/catalog/cluster_resolver.py`
- `tests/agents/catalog/__init__.py`
- `tests/agents/catalog/test_cluster_resolver.py`

Did NOT touch `supabase/seed/source_clusters.sql` (SP3) or anything outside the declared list.

## What ships
- **Pydantic v2 row models** mirroring the on-disk schema: `CatalogSourceRow` (content_sources), `PersonalityRow` (personalities), `ClusterRow` (source_clusters / 0022), `ClusterMemberRef` (source_cluster_members / 0022, source_id XOR personality_id). **Output models:** `ResolvedClusterMember` (kind ∈ {source, personality}, followable_id, display_name, popularity_score) and `ResolvedCluster` (slug, label, category, sort_order, members). `FeedCategory` reused from `agents.pipeline.categories` — no second category map authored.
- **Pure function** `resolve_category_clusters(category, clusters, members, sources, personalities) -> list[ResolvedCluster]`. No DB/network/clock/I/O. Pipeline: (a) filter to category + curated, order by `cluster_sort_order` then `cluster_slug` (deterministic tie); (b) category-wide no-dup set — suppress content_sources rows whose external_id ∈ a present personality's `youtube_channel_ids` (youtube_channel axis) or `aliases` (x_account axis); (c) render members in `member_sort_order`, skipping missing/un-curated rows, suppressed source rows, and first-cluster-wins duplicates; (d) drop clusters left empty after dedup.

## Validation
- **pytest:** PASS — `8 passed in 0.13s` (`pytest tests/agents/catalog/test_cluster_resolver.py -q`).
- **ruff:** PASS — `All checks passed!` (`ruff check agents/catalog/ tests/agents/catalog/`).

## Definition of done (OFFLINE): PASS
All 5 required business-rule cases covered (Rule 9 — WHY in each name/docstring):
1. cluster membership + order — `test_members_returned_in_member_sort_order`
2. empty cluster — `test_cluster_with_only_uncurated_member_is_dropped` (+ missing-row variant)
3. personality dedup (PRD Decision #7) — `test_personality_bundled_handles_are_suppressed_card_shown_once` (+ cross-cluster variant)
4. multi-category row — `test_source_in_two_categories_appears_under_each_call`
5. member in two clusters of one category — `test_followable_in_two_clusters_of_one_category_renders_once_first_wins` (+ empty-after-dedup-drop variant)

## Self code-review (B/C)
Reviewed git diff for purity, no-dup set logic, first-cluster-wins, empty-drop ordering, CLAUDE.md adherence. No critical/high findings.
- Purity: confirmed — only dict/set/sort; no imports of DB/time/io.
- Empty-drop runs AFTER all skips + dedup (correct ordering) — pinned by `test_cluster_emptied_by_duplicate_dedup_is_dropped`.
- Un-curated personality neither renders nor contributes to the no-dup set — internally consistent.

## Concerns / hand-off notes (for M6a consumer)
- **EXACT call signature (stable):** `resolve_category_clusters(category: FeedCategory, clusters: list[ClusterRow], members: list[ClusterMemberRef], sources: list[CatalogSourceRow], personalities: list[PersonalityRow]) -> list[ResolvedCluster]`.
- **This is APP-CALLABLE PYTHON, not SQL.** It consumes already-fetched Pydantic rows; the live Supabase fetch (a `CatalogRepo`) is SP4 / the deferred LIVE-E2E residual.
- **Output models are stable** and minimal (Rule 2). `ResolvedClusterMember.kind` is the source/personality discriminator the UI renders on; `followable_id` is the dedup key (underlying source_id/personality_id).
- `category` is typed `FeedCategory`; in practice one of the 8 topic roots (the only values `cluster_category` can hold per the 0022 CHECK).
