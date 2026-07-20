# Source Reels Spec — YouTube Video Reels + X Theme-of-the-Day

**Why this doc exists:** single source of truth for how followed YouTube channels and X clusters become reels in the daily 30 — the rules `/to-issues` slices against and the pipeline tests encode. Companion to `reference/interview-onboarding-spec.md` (how follows are collected) and `reference/source-catalog-taxonomy.md` (what the catalog looks like).
**When to update:** any change to reel formats, the theme ladder, sweep economics, fallback rules, or the feed-row metadata contract.

## 1. Slot model

`user_feed_allocation` carries `youtube` and `x` rows alongside news categories; sum = 30 (default split **20 news / 7 YouTube / 3 X**; any axis may be 0–30). **News floor:** any source slot that can't be filled honestly falls back to news — never padded, never faked.

## 2. YouTube reels

- **One reel per new long-form video** on a followed channel. Long-form only — Shorts excluded; no minimum length beyond that.
- **Script:** video transcript → summary → reel script. Transcript primary path = yt-dlp caption extraction (already in `agents/ingestion/adapters/youtube.py`); fallback = audio download + own transcription. Both paths failing ⇒ skip the video **loudly**; slot rolls to news.
- **Image:** the video's own thumbnail. No generated poster.
- **Credit:** prominent channel credit + tap-through to the video on every reel (row metadata carries channel name + video URL; TS/Python feed-contract twins).
- **Idempotency:** a video produces one reel ever (cross-day dedup).
- **Upload detection:** keyless RSS, paced (June 2026 throttling incident — see stack-notes).
- **⚠ Gate:** transcript fetching from cloud IPs is **unverified** — M1 live-test from deployed infra decides primary vs fallback before the pipeline is built.

## 3. X theme-of-the-day reels

- **Sweep:** each followed cluster swept **once daily, shared across all followers** — one batched xAI `x_search` call per cluster (hard API cap 20 handles/call; every cluster ≤ 18). ~36 s / ~$0.12 per 20-handle sweep (live-verified 2026-07-04). Cost stays flat with user growth.
- **Input filter:** original posts only — never retweets.
- **Theme extraction:** per cluster, on the shared sweep output. A theme requires **multi-handle support** — one person's loud thread is not a theme.
- **Ladder (per user, per selected category):** first X slot = theme-of-the-day; extra X slots walk down: **theme → second theme → roundup-of-takes → news**. The rung that filled each slot is stamped in `daily_feeds` row metadata for the honest UI label. **Metadata contract (slice #24, migration 0032):** `feed_x_theme_rung` (`theme`/`second_theme`/`roundup`, NULL on a news-floor X slot — real news, never a faked theme) + `feed_x_theme_attribution` (jsonb: `{theme_summary, supporting_handles, supporting_tweet_urls}`). Twins: Python `agents/pipeline/x_theme_ladder.py` (`XThemeReelCandidate`/`XThemeLadderSlot`/`XThemeAttribution`) + assembled by `assemble_niche_feed`; TS `src/types/feed.ts` (`XThemeRung`/`XThemeAttribution`), rendered by `src/lib/reel/sectionChips.ts`.
- **Image (v1):** screenshot of the top tweet (existing renderer; synthetic-poster fallback on render failure). Composite multi-element theme image is v2.
- **Attribution:** the reel credits the handles/tweets the theme came from.
- **Dedup:** two clusters converging on one theme for a user ⇒ one reel.
- **Provider risk:** xAI shut off the predecessor API mid-flight (June 2026). The adapter (`agents/ingestion/adapters/x_account.py`) stays behind a swappable seam; total provider failure ⇒ all X slots to news floor, loud log.

## 4. Per-anchor news scalpel (sub-niche guarantee)

WHO answers carry ≥ 2 concrete anchor terms each. Ingestion: BigQuery entity tags (bulk, batched — unchanged workhorse) + **GDELT DOC 2.0 as a paced nightly scalpel** — single sequential loop, ≥ 5 s between requests, never parallel, never on-demand (live-verified 2026-07-04: violations earn a multi-minute penalty box). Spec: `GDELT_API_specs` (repo root). Guarantee: at least one story for a followed niche on days real news exists, via the shipped niche-first assembly — representation, not domination. **X sweep mentions do NOT count toward this guarantee in v1** (PRD decision 9).

## 5. Supply expectations

Shown at selection time in onboarding: per-channel cadence guesstimates (videos/week) live in the catalog; the picker sums selections ("these 12 channels ≈ ~5 long-form videos/day"). Guesstimate quality is acceptable v1; no live API calls during onboarding.

## 6. Seed catalog (launch asset — v2)

8 roots × 15 sub-niches (120 depth-1 `interests` nodes), 170 YouTube channels, 121 X clusters (~1,055 handles, all ≤ 18/cluster, one cluster per sub-niche). Canonical data: `scripts/seed_catalog/data/root_catalog_v2.json` (parsed 2026-07-04 from artifact bd94cf30). Schema already on prod: migrations 0022 (`source_clusters`/`source_cluster_members`) + 0028 (`cluster_subniche`, `cluster_description`). Every new handle live-verified before seeding (the expansion is ~5× and best-effort per the artifact's gaps table). No-dup rule: a followable personality bundles their handles, shown once.

**Avatars (one-time, owned):** every catalog row gets a small (~128 px WebP) avatar fetched once — YouTube via Data API thumbnails (verified), X profile images (source TBD at slice time: unavatar is capped ~25 req/day anonymous; syndication endpoints dead) — stored in the public `source-avatars` bucket with `thumbnail_url` pointed at our copy. Never hot-linked, never refreshed; missing avatar ⇒ initials fallback.
