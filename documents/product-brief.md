# Product Brief — Chat Onboarding + YouTube/X Source Reels

**Date:** 2026-07-04
**Status:** Draft — needs `/cto` to translate into a PRD
**Scope:** Revamp of an existing product (blip / News20). Supersedes the 2026-07-03 brief (single-select tap-through interview — built in slice #4, rejected by the founder on sight) and formally supersedes the FSR "roots-only, no drilling" thesis. Prior briefs in git history.
**Design handoff (canonical):** Claude Design project `2bd1f0ab-87f5-4392-a699-8e3e6e5a1c68`, file `News20 Prototype/Onboarding Chat.html` (+ `onboarding-chat.js`). Local copies: `reference/design-handoff/onboarding-chat.{html,js}`. The prototype is a deterministic script; the build keeps its **interaction contract** and replaces canned copy/branching with the real engine.

## One-liner

Onboard in one ChatGPT-style conversation; every morning get 30 stories — news, your YouTube channels, and your X people.

## Target user

The founder is user #1: a news-heavy professional who checks YouTube subscriptions and X every morning on top of news apps, and has specific sub-niches (IPL, frontier AI labs, Indian markets) no broad category captures. The moment: first app open — and every morning after.

## Problem

Two problems, one product. (1) The shipped interview is single-select tap-through — a workflow wearing chat clothes; users can't say "I follow AI *and* geopolitics *and* markets" or pick several sub-niches at once, and it silently forces one path. (2) The daily 30 is news-only; the user's actual morning sources — YouTube channels and X voices — aren't in the feed, so the app doesn't replace the morning ritual, it adds to it.

## Today's workaround

The user manually checks YouTube subscriptions + X + a news app every morning. In-product: the slice-#4 interview and the M6a grid-based source screen exist but don't match how the founder wants selection to feel or work.

## Unique angle

1. **The whole onboarding is one chat scrollback** — multi-select bubble chips, an always-available composer for free typing, budget card and source pickers *inside the chat*. Answered steps collapse into user bubbles; the history stays visible.
2. **X theme-of-the-day reels per category**: one reel that finds the story your followed cluster is all reacting to — with an honest ladder (theme → roundup-of-takes → news) on quiet days. Nobody else turns "what my corner of X is saying" into a daily reel.
3. **YouTube reels from your own channels**: one reel per long-form video, script from the transcript, the video's own thumbnail as the reel image.

## The onboarding contract (locked in the design prototype)

1. **INTERESTS** — multi-select category chips → per selected category, multi-select sub-niche chips + live composer for typing custom interests → **one open-ended WHO drill per selected sub-niche** ("Cricket — a series, a player? Name it and I'll follow it"), skippable via "Nothing specific →". Real engine generates sub-niche bubbles and drill wording dynamically.
2. **TUNE** — 3–4 quick single-tap follow-ons **conditional on picks** (dynamically generated). Fixed jobs per category: **ANGLE** (which lens grabs you) and **SKIP** (mute list — *missing from the prototype, must be added*); WHO is already covered by the drill step. Then the **story budget card** with ± steppers, total pinned, "Lock →".
3. **YOUTUBE** — ~30 channel tiles sorted by profile relevance, multi-select, in-chat.
4. **X CLUSTERS** — curated handle clusters grouped by the user's categories (+ "beyond your picks"), samples + expandable handle list, multi-select, in-chat.
5. **YOUR 30** — summary card (news / YouTube / X split) → "Build my 30 →".

Skip semantics: every question skippable; consecutive skips fast-forward (skip first follow-up → offer to skip the category; two categories skipped → offer "just build my feed"). Skipped questions resurface later in-app, one at a time. Interview re-run = existing rebuild-my-feed entry, clean-replace semantics.

## Feed rules (locked)

- **Defaults are defaults only** — the user can re-mix the 30 all the way to all-news or all-X, in the budget card and later in Build-your-30.
- **YouTube**: one reel per video, **long-form only** (exclude Shorts; no minimum beyond that). Reel = transcript → summary → script, video thumbnail as image, prominent channel credit + tap-through to the video.
- **X**: original posts only, never retweets. Per selected category, first X slot = **theme-of-the-day** reel; extra X slots walk down the ladder (second theme → roundup → news). v1 reel image = screenshot of the top tweet (existing renderer); composite multi-element theme image is v2.
- **News floor**: any unfillable source slot falls back to news. Never padded, never faked.
- **Supply expectations shown at selection time**: "these 12 channels ≈ ~5 long-form videos/day" (guesstimate is fine) so slot counts stay grounded.
- **Sub-niche guarantee**: at least one story for a followed niche on days news exists — via per-anchor entity queries + the already-shipped niche-first assembly (slice #7). Representation, not domination.

## Ingestion (verified where marked)

- **News wide sweep** — unchanged, in production (GDELT via BigQuery + trusted outlets).
- **News per-anchor queries** — WHO answers carry ≥2 concrete search-anchor terms each; run through BigQuery entity tags (bulk, unthrottled) + GDELT DOC 2.0 as a paced nightly scalpel. **Live-verified 2026-07-04**: DOC 2.0 returned real Vaibhav Suryavanshi articles; hard limit ~1 request/5s/IP with multi-minute penalty box — single paced loop only, never parallel, never on-demand. Spec: `GDELT_API_specs` (repo root). GDELT-only in v1; vertical APIs (cricket etc.) only if validation shows persistent misses.
- **X** — xAI Agent Tools `x_search` (adapter already in repo: `agents/ingestion/adapters/x_account.py`). **Live-verified 2026-07-04**: batches work; hard API cap **20 handles per call**; 20-handle sweep ≈ 36s, ~$0.12. Sweep each cluster **once daily, shared across all followers** — cost stays flat with user growth. Keep every cluster ≤18 handles.
- **YouTube** — upload detection (RSS/Data API; June throttling incident known, fixable with pacing/API key) + thumbnail (trivial) + **transcripts: UNVERIFIED and the weakest link** — YouTube blocks cloud-IP transcript fetching unpredictably. **Test-first, before building the pipeline**; fallback = audio download + own transcription (pennies/video).

## Seed catalog

Report (2026-07-04): 8 roots, ~71 YouTube channels, 22 X clusters (~230 handles, all ≤18). ~60/71 channels already exist in the repo's raw catalog — the work is reorganizing, not sourcing. India-first variants for politics/cricket/markets/arts. Flagged-for-verification handles listed in the report's gaps table. Artifact: https://claude.ai/code/artifact/bd94cf30-abbb-4e2e-b665-bd3e99d7e67f

## Smallest provable version

Ships as one chain: chat onboarding (contract above) + profile persistence + YouTube pipeline + X theme reels with ladder + defaults re-mixable in the budget card. Deferred: composite theme images (v2), vertical sports API, in-app resurfacing of skipped questions (can follow fast).

**Sequenced inside the chain: YouTube transcript live-test first** — it's the only unproven pipe.

## 90-day success metric

*(Inferred — founder approved by moving to build; sharpen at /office-hours.)* The feed replaces the founder's morning YouTube/X check: ≥25 of 30 slots filled without fallback on a typical day, and every followed niche with real news represented same-day. Interview completion without skip-out on the founder's own runs.

## Competition

Google News/Artifact-style aggregators (broad-topic, no conversational profile, no source reels); the user's own YouTube/X apps (the thing being replaced); do nothing = the shipped single-select interview the founder already rejected.

## What held up under pressure

- Theme-of-the-day over roundup for X — with the ladder making quiet days honest instead of empty.
- One-sweep-per-cluster economics (flat cost with growth).
- WHO/ANGLE/SKIP as fixed jobs with dynamic wording — complete profile per category, readable back in the user's own words.
- Skip-spam as the early exit (with fast-forward).
- Creator credit + tap-through as both the optics mitigation and good UX.
- "You mostly already own the catalog" — 85% seeded.

## What's still soft

1. **YouTube transcripts** — unverified; the plan's weakest link. Test before build.
2. **xAI dependency** — proven today, but the provider shut off its predecessor mid-flight in June; keep the adapter swappable, news floor always on.
3. **90-day metric** — never explicitly confirmed by the founder.
4. **Catalog handle accuracy** — India + F1 handles flagged for live verification before seeding.
5. **Onboarding length tolerance** — multi-category × drills is a lot of taps; skip design mitigates, zero observation yet.

## Riskiest assumption

**That the YouTube transcript pipe works reliably from our infrastructure.** Everything else is verified or already in production. If transcripts fail from cloud IPs, the audio-transcription fallback becomes the plan — slower and slightly costlier, but it must be proven before the YouTube half is promised.

## Contradictions surfaced

1. **Slot defaults: spoken "6 YouTube / 4 X" vs the design's "20 news / 7 YouTube / 3 X".** Unresolved by the founder in-session. Recommendation: **follow the design (20/7/3)** — it's the newer, founder-authored artifact and the budget card math is built around it. Flag at /cto if disagreement.
2. **Design's 9 categories (incl. Health, Climate, Culture, Markets) vs the backend's 8 roots.** The prototype's list is illustrative. Needs a /cto decision: map design categories onto the 8 existing roots, or extend the taxonomy.
3. **Prototype has no SKIP (mute-list) question.** The founder locked who/angle/skip; TUNE must gain the skip job even though the design doesn't show it.
4. **FSR "roots-only" thesis vs deep drilling** — formally superseded: the product drills deep on purpose; update `documents/feed-source-revamp-plan.md` when the PRD lands.

## Open questions

1. Slot-default contradiction above (20/7/3 vs 26/6/4-ish — pick one).
2. Category taxonomy mapping (design 9 vs backend 8 roots).
3. Where TUNE's dynamic questions come from (engine prompt design — /cto).
4. Migration of existing profiles (founder's real account) to the new profile shape.
5. Whether X cluster sweeps also feed the sub-niche news guarantee (a cluster mention of Suryavanshi counting toward his representation) or stay separate.
