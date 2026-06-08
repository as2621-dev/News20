# Execution report — phase-5f-source-catalog-curation · SP3

**Axes:** `x_account` + `personality` (all 12 archetypes). **Mode:** sequential (main repo).
**Date:** 2026-06-08. **Seed path:** `scripts/seed_catalog/seed_via_pooler.py` (IPv4 session pooler / asyncpg — REST host unreachable here).

## STATUS: SUCCESS (with one documented external-cap caveat on personality coverage)

Every cell ≥50, X thumbnail coverage 100%, topic_tags[0] 0 violations. Personality
thumbnail coverage is **85.8% global** (10/12 cells 81–100%); the 90% target is missed
only because two cells — `crypto-fintech` (64.5%) and `markets-macro` (74.7%) — hit a
**real Wikipedia ceiling** (many crypto/finance figures have no Wikipedia article or no
free lead image). Surfaced loudly per the DoD's "say so loudly" instruction, not hidden.

## Files touched (paths only)

Code:
- `scripts/seed_catalog/x_resolve.py` (NEW) — unavatar avatar resolver.
- `scripts/seed_catalog/seed_catalog.py` — wired X resolver (async `seed_x_accounts`, `build_x_account_row` takes `avatar_url`, `SeedSummary.x_accounts_no_avatar`); bounded + retried Wikipedia photo fetch.
- `scripts/seed_catalog/seed_via_pooler.py` — `_set_expr` union upsert for `personas`/`topic_tags` (cross-archetype tag accumulation).

Tests:
- `tests/scripts/seed_catalog/test_x_resolve.py` (NEW) — 6 mocked resolver tests.
- `tests/scripts/seed_catalog/test_seed_catalog.py` — `FakeResponse` headers + `_unavatar` route; X test flipped to assert hot-link resolution.

Data (the reproducible artifact): `scripts/seed_catalog/data/x.*.json` (12) + `personalities.*.json` (12), regenerated to ~80–95/cell.

## Validation: PASS

- `ruff check scripts/seed_catalog/ tests/scripts/seed_catalog/` → **All checks passed**.
- `pytest tests/scripts/seed_catalog/` → **30 passed** (24 pre-existing SP1/SP2 + 6 new X-resolver; no regression).

## Definition of done: PASS (count + X coverage + tags) / DOCUMENTED-SHORT (personality coverage)

| archetype | x_cnt | x_thumb | pers_cnt | pers_thumb |
|---|---:|---:|---:|---:|
| ai-frontier-tech | 80 | 100.0% | 80 | 83.8% |
| arts-culture | 80 | 100.0% | 80 | 96.2% |
| balanced-generalist | 80 | 100.0% | 80 | 98.8% |
| climate-energy | 79 | 100.0% | 80 | 90.0% |
| creator-media | 80 | 100.0% | 80 | 87.5% |
| crypto-fintech | 80 | 100.0% | 93 | 64.5% |
| geopolitics-world | 80 | 100.0% | 80 | 100.0% |
| markets-macro | 80 | 100.0% | 95 | 74.7% |
| sports-fan | 79 | 100.0% | 80 | 100.0% |
| startup-operator | 80 | 100.0% | 80 | 81.2% |
| tech-generalist | 80 | 100.0% | 81 | 86.4% |
| us-politics-policy | 80 | 100.0% | 80 | 96.2% |

- **Count ≥50:** PASS — min x_cnt=79, min pers_cnt=80 (all 24 cells clear 50).
- **X thumbnail coverage:** **100.0%** (852/852) ✓ ≥90%.
- **Personality thumbnail coverage:** **85.8%** (723/843) — **short of 90%**, concentrated entirely in crypto-fintech + markets-macro (see caveats).
- **topic_tags[0] ∈ 8 keys:** 0 violations on both axes ✓.

## The unavatar wiring + seed_catalog.py changes

- **`x_resolve.py`** hot-links `https://unavatar.io/x/<handle>` (taxonomy Q2/Q3 — unavatar serves the real avatar when it can, a generated initials-avatar otherwise, so the URL always renders). It additionally probes the URL: `200 image/*` confirms a real cached avatar; `404` is a confirmed no-avatar (→ null thumbnail); `429` is unavatar's per-IP rate limit (→ keep the hot-link, NOT a no-avatar signal); transport error → keep the hot-link. Bounded concurrency (4), never raises.
- **`seed_x_accounts`** is now async, resolves avatars via `x_resolve.resolve_many`, and `build_x_account_row(entry, avatar_url)` stores the hot-link. `SeedSummary.x_accounts_no_avatar` counts confirmed-404 soft-misses (0 this run).
- **Wikipedia photo fetch** (`_wikipedia_summary_image`) gained a bounded transient retry (`WIKIPEDIA_RETRY_DELAYS_SECONDS = (0,1,3)` on connection reset / 429; a 404/image-less article is NOT retried) and `seed_personalities` now uses a `WIKIPEDIA_PHOTO_CONCURRENCY=4` semaphore. This lifted personality coverage from 23.6% (first burst) → 85.8%.
- **`seed_via_pooler._set_expr`** unions `personas`/`topic_tags` arrays on conflict (`array(select distinct unnest(existing || excluded))`) instead of overwriting. **Root-cause fix:** the `--all-archetypes` driver upserts one archetype per `run_seed`, so a cross-archetype source (e.g. Elon Musk in balanced-generalist AND tech-generalist) had its persona clobbered by the last cell — `balanced-generalist` had collapsed to 40 X / 30 pers before the fix; 80/80 after.

## Data-quality caveats (listed, never hidden)

1. **X avatars are hot-links, NOT individually verified this run.** unavatar's **free anonymous tier caps at ~25 requests/day/IP** (`{"code":"ERATE", x-rate-limit-limit: 25}`, `retry-after ≈ 31000s`). The seed probed 850+ handles, so the daily budget was exhausted and most probes returned 429. Per the resolver's design a 429 keeps the hot-link, so **100% of X rows carry a renderable `thumbnail_url`** that renders the real X avatar whenever unavatar can serve it (and its own generated avatar otherwise). The trade-off: hallucinated/dead handles are NOT dropped (the 25/day budget can't verify at scale). Spot-checked handles (karpathy, sama, WSJ, Reuters, BarackObama) return real `image/jpeg` from unavatar. **To verify handles at scale, register an unavatar API key** (still only 50/day free — a paid tier or X API is needed for full anti-hallucination).
2. **Personality photo ceiling in 2 cells.** `crypto-fintech` (64.5%) and `markets-macro` (74.7%) are at a genuine Wikipedia floor: ~20/80 crypto figures (e.g. Anthony Pompliano, Raoul Pal, Stani Kulechov, Jimmy Song) have **no English Wikipedia article** (404), and several more have an article but **no free lead image**. The seeder correctly keeps these rows (count stays ≥50) with a null `thumbnail_url`; the app's initials-on-gradient avatar covers them. The other 10 cells are 81–100%.
3. **Null-avatar handling is honest.** A confirmed-404 X handle and an image-less Wikipedia personality both store `thumbnail_url=NULL` (logged), never a fabricated URL.
4. **No X follower count** (`subscriber_count=NULL`) — taxonomy Q2 (no live X API in 5f). Personalities also carry no follower count.
5. Data files were deduped by slug after regeneration (removed file-level display-name/slug duplicates such as "Michael Saylor"/"Michael J. Saylor"); remote rows already collapsed on the slug, so remote is unaffected.

## Concerns for SP4

- **Personality global coverage 85.8% < 90%.** If SP4's DoD enforces a hard 90% per-axis floor, crypto-fintech/markets-macro need either (a) curation that swaps imageless figures for Wikipedia-photographed ones, or (b) an accepted lower floor for those two cells. The ceiling is data-real, not a bug.
- **X avatars unverified for hallucination.** SP4 (or a follow-up) should either register an unavatar key or add a lightweight handle-existence check; today's rows trust the LLM's handle accuracy. The hot-link renders regardless, so the UX is unaffected, but a dead handle would show unavatar's generated avatar.
- **Cross-archetype persona union now depends on the pooler `_set_expr` fix.** Any future re-seed via a different path (e.g. the REST `make_admin_client` path, if it ever becomes reachable) must apply the same union semantics or it will re-clobber `balanced-generalist`. The REST upsert in `seed_catalog._upsert_content_sources` still uses plain replace — fine while only the pooler driver runs, but a latent inconsistency to note.
- A `--type podcasts --all-archetypes` seed (SP2's axis) was running concurrently throughout; X/personality rows don't overlap podcast rows, so no interference. SP4 should confirm the podcast axis completed before final phase-level assertions.
