# Residual review findings — slice #15 (greenfield v2 seeder)

Reviewed commit `8b01492` (feasible-half v2 seeder). Multi-agent panel ran
correctness, data-integrity, and simplicity/reuse lenses. All **concrete** fixes
were applied in the follow-up commit (see below). The items here are **advisory /
human-decision** residuals that were deliberately NOT changed in this slice.

## Applied in follow-up commit (not residual — listed for the record)
- `find_source` now normalizes `ltrim(external_id,'@')` so the plan-time gate and
  seed-time lookup share ONE membership predicate (was a latent asymmetry).
- `seed_clusters` resolves members BEFORE upserting the structure and skips (never
  commits) a cluster whose live members all vanished — enforces "not seeded empty"
  at the write boundary too, not just at plan time.
- Drop-ratio sanity gate denominator = unique handles attempted (was raw per-root
  count, which a cross-root duplicate could inflate and weaken the gate).
- `build_interest_rows` fails loud on an intra-batch `interest_slug` collision
  (two sub-niches slugifying identically would otherwise silently drop the second).
- Dead fields removed (`already_seeded` on the models, `resolved_count`); the
  `_PoolerClient` shim dropped from `seed_channels` in favour of a direct `_flush`.
- New tests cover the drop-gate halt and the slug-collision fail-loud.

## Residual — advisory / human decision (deferred, not fixed here)

### R1 — Minted interest nodes have NULL `interest_search_query` (functional, human decision)
The 120 depth-1 nodes are seeded with slug/label/depth/parent/sort only. Per the
known gotcha (`news20-interest-query-gap`), **ingestion skips interest picks that
have no `interest_search_query`** — a user who follows one of these new sub-niches
gets no ingestion until a query is back-filled. This slice intentionally seeds the
taxonomy scaffolding only (the X-handle + query-backfill work is gated on the #27
verification-method decision). **Decision needed:** back-fill `interest_search_query`
for the 120 sub-niche nodes (own slice) vs. rely on migration 0025's on-follow RPC.
Not a bug in the seeder — a scope boundary to make explicit before these nodes are
surfaced as followable in onboarding.

### R2 — Inline channel-row builder duplicates `seed_catalog.build_channel_row` (drift risk, low)
`resolve_channels` builds the `content_sources` row inline rather than reusing
`seed_catalog.build_channel_row`. The reuse isn't free (v2 has no `CatalogEntry`,
adds `platform_metadata`, hardcodes `personas=[]` / `popularity_score=50.0`), so the
divergence is deliberate. Risk: if `content_sources` gains a NOT-NULL column, the two
builders drift silently. Accepted for now; revisit if the row shape changes.

### R3 — Array UNION (`topic_tags`/`personas`) order not stable across re-runs (harmless)
`_flush`'s `array(select distinct unnest(...))` preserves membership but not order,
so tag arrays may reorder on re-seed. Reads filter on `&&` membership, so this is
cosmetic only. No action.
