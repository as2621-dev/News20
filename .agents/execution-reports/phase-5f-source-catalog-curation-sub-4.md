# Phase 5f · Sub-phase 4 — Remote verification + reproducibility + e2e

**Status:** SUCCESS (executed by the orchestrator).

## Remote assertions (asyncpg over the IPv4 session pooler) — all PASS
Total `content_sources`: **3360 rows**. Every one of the 12 archetypes × 4 axes (48 cells) holds ≥50 persona-tagged rows:

| axis | total | thumb coverage | min thumbnail-bearing/cell | cells ≥50 |
|---|---|---|---|---|
| youtube_channel | 633 | 100% | 57 | 12/12 |
| podcast | 1032 | 100% | 68 | 12/12 |
| x_account | 852 | 100% (renderable) | 79 | 12/12 |
| personality | 843 | 85.8% real photos | 60 | 12/12 |

- `topic_tags[0]` ∈ the 8 pinned keys: **0 violations** across all rows.
- **Phase-level DoD MET:** ≥50 thumbnail-bearing, archetype-tagged rows for every profile on every axis — including the niche personality cells (crypto-fintech, markets-macro), which still have ≥50 rows *with* a Wikipedia photo even though their global photo rate is <90%.

## e2e proxy (the exact runtime query, not the iOS simulator)
Ran `listSourcesByArchetype`'s query — `personas && ARRAY[slug]` + type filter, `order by popularity_score desc limit 12` — for **balanced-generalist** (the slug the `SKIP_AUTH=true` build always resolves to). All 4 axes return a full **12-card, 100%-thumbnail** deck of recognizable sources:
- channels: Lex Fridman, Kurzgesagt, BBC News, Al Jazeera, TED, NYT…
- podcasts: Lex Fridman, The Daily, Joe Rogan, Bill Simmons, Bankless, Acquired…
- x: NYT, Greta Thunberg, Bloomberg, Elon Musk, WaPo, Bill Gates…
- personality: Warren Buffett, Joe Biden, Messi, Greta Thunberg, Ray Dalio, Ronaldo…

The "0 sources followed across YouTube, Podcasts, X & People" screen is gone at the data + query layer.

**iOS simulator render explicitly DEFERRED** (Rule 12): the native build + `npx cap sync ios` + simulator screenshot is deferred to the cloud-Mac iOS session, consistent with the existing iOS deferral (memory `news20-phase3b-ios-deferred`). The data-layer proxy above proves the deck will populate.

## Reproducibility
Committed: all 48 `data/{channels,podcasts,x,personalities}.*.json` candidate inputs + the generator + resolvers + the pooler driver. Re-running `seed_via_pooler --type <axis> --all-archetypes` regenerates the catalog deterministically (modulo live-API availability).

## Slop scan — PASS
No TODO/FIXME/HACK, no stray prints, no swallowed excepts, no `type: ignore`/casts, no hardcoded secrets, no secret logging across all touched code.

## CSO (lite security) — PASS
- **Secrets:** none in code; all keys read from env (`SUPABASE_DB_URL`/`YOUTUBE_API_KEY`/`GEMINI_API_KEY`), never logged (verified).
- **SQL construction** (`seed_via_pooler._flush`): column names are validated `^[a-z_]+$` before interpolation and values are bound parameters (`$1…$n` via asyncpg) — no injection surface. Conflict targets are a fixed allow-list.
- **Dependency:** `asyncpg>=0.29` added to `requirements.txt` (was installed ad-hoc) — actively maintained, not a typosquat.
- **Write auth:** the pooler connects as the `postgres` owner (bypasses RLS by design — the only catalog writer), matching the seeder's service-role intent.

## Accepted v1 limitations (documented, not hidden)
1. **X handles unverified** — unavatar free tier is 25 probes/day/IP; exhausted → most handles kept as renderable hot-links without realness verification. In-scope per taxonomy Open-Q2 (real X API is a later upgrade).
2. **Personality real-photo coverage 85.8%** (crypto-fintech 64.5%, markets-macro 74.7%) — a real Wikipedia ceiling; every cell still has ≥50 photo-bearing rows, so the phase DoD holds. Rows without a photo use the app's initials avatar.

## Out of scope (unchanged)
Turning off `SKIP_AUTH` / magic-link deep-linking so follows persist in the simulator; periodic catalog refresh; content-item ingestion (5d).
