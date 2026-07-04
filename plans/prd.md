# PRD — Chat Onboarding + YouTube/X Source Reels

**Date:** 2026-07-04
**Source:** documents/product-brief.md (brainstormed + approved 2026-07-04)
**Status:** Ready for /to-issues
**Design handoff (canonical):** `reference/design-handoff/onboarding-chat.{html,js}` — the prototype's **interaction contract** is locked; its canned copy/branching is replaced by the real engine.

> **Supersession notice (read first).** This PRD supersedes two things:
> 1. **The slice-#4 single-select tap-through interview UI** (rejected by the founder on sight). The interview **engine** (`agents/interview/` — already multi-select: `bubbles_tapped: list`, stateless turn protocol, guardrails, terminal schema) **stays and is extended**; only the interaction layer is replaced by the chat contract below.
> 2. **The FSR "roots-only, no drilling" thesis** (documents/feed-source-revamp-plan.md §2.1) — formally dead; the product drills deep on purpose. Everything else FSR shipped stays load-bearing: authority-weighted importance, trusted-outlet backbone, theme→category tagging, M6b source priority slots.
>
> The 2026-07-03 PRD's niche machinery (minted tree nodes, niche ingestion, fallback-ladder assembly, section rendering — slices #1–#9, shipped) **stays and is built upon**. Open issues #10 (persona validation), #11 (census), #12 (Build-My-30 niche sections) remain valid work. Old PRD recoverable at git `7ff7c68` and earlier.

## Problem Statement

Two problems, one product. (1) The shipped interview is single-select tap-through — a workflow wearing chat clothes; users can't say "I follow AI *and* geopolitics *and* markets" or pick several sub-niches at once, and it silently forces one path. (2) The daily 30 is news-only; the user's actual morning sources — YouTube channels and X voices — aren't in the feed, so the app doesn't replace the morning ritual, it adds to it.

## Solution

From the user's perspective:

- **Onboarding is one ChatGPT-style conversation.** One scrollback: multi-select bubble chips, an always-available composer for free typing, the story-budget card and the YouTube/X pickers *inside the chat*. Answered steps collapse into your own bubbles; the history stays visible. Every question is skippable, and skipping fast-forwards.
- **Every morning: 30 stories — news, your YouTube channels, your X people.** Default split 20 news / 7 YouTube / 3 X, re-mixable all the way to all-news or all-X.
- **YouTube reels from your own channels**: one reel per long-form video — script from the transcript, the video's own thumbnail as the image, prominent channel credit and tap-through.
- **X theme-of-the-day reels per category**: one reel that finds the story your followed cluster is all reacting to — with an honest ladder (theme → second theme → roundup-of-takes → news) on quiet days.
- **Your niches stay guaranteed**: at least one story for a followed niche on days real news exists, via the shipped niche-first assembly.

## Technical Foundation

### Tech stack (existing, fixed — one-line rationale each)

- **Frontend:** Next.js 15 static export + React 19 + Tailwind 4 + Capacitor 8 — the shipped app shell; the chat onboarding replaces the `interview` stage inside `OnboardingFlow.tsx`'s existing state machine and absorbs the `sources` + `build` stages into the chat.
- **Backend/data:** Supabase (Postgres + RLS + email-OTP auth) — all onboarding outputs land in existing/extended tables.
- **Agent layer:** Python 3.12 worker (FastAPI on Railway) using `google-genai`, Gemini Flash-class structured JSON — the shipped interview engine is extended (multi-select turns, WHO drills, TUNE), keeping prompts + keys server-side.
- **News ingestion:** GDELT BigQuery (backbone + bulk entity tags, unchanged) **plus** GDELT DOC 2.0 as a paced nightly scalpel for per-anchor queries — live-verified 2026-07-04 (~1 req/5 s/IP, multi-minute penalty box; single paced loop only). Spec: `GDELT_API_specs` (repo root). GDELT-only in v1; vertical APIs only if validation shows persistent misses.
- **X ingestion:** xAI Agent Tools `x_search` (adapter exists: `agents/ingestion/adapters/x_account.py`) — live-verified 2026-07-04; hard cap **20 handles/call**, 20-handle sweep ≈ 36 s / ~$0.12. New: batched **per-cluster** sweep (clusters ≤ 18 handles), once daily, shared across all followers. Adapter stays swappable (provider killed its predecessor mid-flight in June).
- **YouTube ingestion:** keyless RSS upload detection + yt-dlp caption extraction (**both already in `youtube.py`**) — the risk is cloud-IP blocking, not missing code; fallback = audio download + own transcription (pennies/video). **Live-verified from deployed infra before anything else is built (M1).**
- **Jobs:** Trigger.dev v4 daily batch — unchanged.
- **Hosting:** Vercel (SPA) + Railway (worker) — unchanged.
- **Languages:** TS (app) + Python (pipeline) — unchanged.

### Architecture

```
 Onboarding chat (SPA)                          Worker (FastAPI, Railway)
 ┌─────────────────────────────────┐  POST /api/interview/turn  ┌─────────────────────────┐
 │ ONE chat scrollback:            │ ─────────────────────────► │ Interview engine (ext.) │
 │ INTERESTS → TUNE(+budget card)  │ ◄───────────────────────── │ multi-select, WHO drill,│
 │ → YOUTUBE → X CLUSTERS → YOUR 30│   bubbles/drills/terminal  │ TUNE angle+skip, Gemini │
 └───────────────┬─────────────────┘                            └─────────────────────────┘
                 │ persist: interests+profile+mutes+allocation+source/cluster follows
                 ▼
 ┌─────────────────────────────────┐      ┌──────────────────────────────────────────────┐
 │ Supabase                        │      │ Daily pipeline (Python batch)                │
 │ interests / user_interest_profile│◄────│ NEWS: backbone (BigQuery, unchanged)         │
 │ user_mute_terms (new)           │      │  + per-anchor scalpel (BQ entity tags +      │
 │ user_feed_allocation (20/7/3)   │      │    paced GDELT DOC loop)                     │
 │ user_content_sources            │      │ YOUTUBE: RSS sweep → long-form filter →      │
 │ source_clusters + members (new) │◄────│    transcript (yt-dlp | audio fallback) →     │
 │ cluster_themes (new, shared)    │      │    script → reel (video thumbnail)           │
 │ daily_feeds (+theme metadata)   │      │ X: per-cluster x_search sweep (once/cluster) │
 └───────────────┬─────────────────┘      │    → theme extraction → theme reel ladder    │
                 ▼                        │ ASSEMBLY: niche-first + source slots +       │
 Reel: news + YT reels (credit+tap-out)   │    theme slots + news floor                  │
       + X theme reels (tweet screenshot) └──────────────────────────────────────────────┘
```

### Key design decisions

1. **Slot defaults are 20 news / 7 YouTube / 3 X** (the design's numbers, not the spoken 6/4). Why: the newer, founder-authored artifact; the budget-card math is built around it. Defaults only — the YOUR-30 card re-mixes the top-level split 0–30 per axis, and Build-your-30 keeps full control later. Rules out: hard slot minimums per axis.
2. **The category chips are the backend's 8 roots** (`ai, geopolitics, business, environment, politics, tech, sport, arts` — labels from `ROOT_LABEL_BY_SLUG`); the prototype's 9-category list is illustrative. Markets→business, Culture→arts, Climate→environment, World & Politics→geopolitics+politics. **Health gets no root in v1** — health-type typed interests park under the nearest root or root-level; extend the taxonomy only if validation shows demand (flagged for revisit). Why: a 9th root touches enum, allocator, theme-tagger, and ranker for an unproven category. Rules out: taxonomy migration in this chain.
3. **One engine drives the whole conversation.** The shipped stateless turn protocol (`POST /api/interview/turn`, state travels with the request, HTTP-200 typed-retry failure contract) is extended with new turn kinds: multi-select sub-niche turns, one open-ended WHO drill per selected sub-niche, and TUNE turns. The client stays a dumb renderer of the design prototype's interaction contract. Rules out: client-side branching, a second endpoint, server sessions.
4. **TUNE = fixed jobs, dynamic wording.** Per selected category the engine generates 3–4 single-tap follow-ons covering two code-enforced jobs: **ANGLE** (which lens grabs you) and **SKIP** (mute list — absent from the prototype, mandatory here); WHO is already covered by the drill step. Option bubbles ≤ 6, deterministic fallback copy if the LLM fails, answers traceable to taps (never invented). SKIP answers persist as per-category mute terms applied at assembly (never at ingestion — the pool is shared). Rules out: a static question bank; mute-by-downranking (mutes are hard filters).
5. **Skip semantics are engine-owned state-machine rules, not LLM judgment.** Every question skippable; skip the first follow-up of a category → offer to skip the category; two consecutive categories skipped → offer "just build my feed". Skipped questions are recorded as deferred (in-app resurfacing is a fast-follow, but the *record* is written now). Interview re-run = existing rebuild-my-feed entry, clean-replace semantics. Skip-everything degenerate case = roots-only profile; feed works exactly as today.
6. **YouTube reels: one reel per long-form video, transcript-scripted, thumbnail-postered.** Long-form only (exclude Shorts; no minimum beyond that). Script = transcript → summary → reel script; image = the video's own thumbnail (no generated poster); prominent channel credit + tap-through to the video. Caption-less videos fall to the audio-transcription path, not silence. Why: the video's own assets are both cheaper and the honest representation. Rules out: generated posters for YT reels, Shorts ingestion.
7. **X: sweep per cluster, once daily, shared across all followers.** Clusters are curated handle groups (≤ 18 handles, under the 20-handle API cap) in `source_clusters`/`source_cluster_members` (schema from `reference/source-catalog-taxonomy.md`, now migrated for real). Original posts only, never retweets. Theme extraction runs per cluster on the shared sweep output; per user+category, the first X slot = theme-of-the-day reel, extra X slots walk the ladder (second theme → roundup-of-takes → news). v1 theme-reel image = screenshot of the top tweet (existing renderer); composite image is v2. Why: cost stays flat with user growth (~$0.12/cluster/day). Rules out: per-user sweeps, on-demand X calls.
8. **News floor everywhere.** Any unfillable source slot (no videos today, cluster silent, theme too thin) falls back to news. Never padded, never faked. The theme ladder's last rung IS the news floor.
9. **X sweeps do NOT feed the sub-niche news guarantee in v1.** A cluster mention of a followed niche does not count as news representation; the guarantee stays GDELT-based (per-anchor scalpel + shipped niche-first assembly). Why: mixing tweet-derived signal into news representation creates provenance and dedup complexity before either pipe is validated. Flagged for revisit after M5.
10. **Supply expectations are shown at selection time from catalog cadence estimates.** Each catalog channel carries a rough videos/week guesstimate; the YouTube picker sums selections into "these 12 channels ≈ ~5 long-form videos/day". Guesstimate quality is explicitly acceptable v1. Rules out: live API calls during onboarding.
11. **Per-anchor news scalpel = BigQuery entity tags (bulk) + one paced GDELT DOC loop.** WHO answers carry ≥ 2 concrete anchor terms; BigQuery remains the batched workhorse; DOC 2.0 runs as a single sequential nightly loop (1 req/5 s, never parallel, never on-demand) for anchors BigQuery misses. Rules out: DOC fan-out, daytime DOC calls.
12. **Existing profiles migrate by re-interview** (rebuild-my-feed, clean replace) — nothing to transform; the founder's real account runs the new chat once.

### Module contracts (plain prose — test intentions, not tests)

- **Chat onboarding engine (worker, extends `agents/interview/`).** Responsibility: given conversation state, return the next turn (question + bubbles/drill/TUNE options) or the terminal profile (micro-interests + mutes + angles + deferred-skips). Must always: honor phase order INTERESTS → TUNE; enforce multi-select where the contract says multi-select; issue exactly one WHO drill per selected sub-niche, skippable via "Nothing specific →"; enforce ANGLE + SKIP jobs per category with ≤ 6 options; apply the skip fast-forward rules deterministically in code; keep the shipped terminal validation (root-anchored slugs, ≥ 2 anchor terms, nothing invented). Edge cases: user selects many categories × many sub-niches (drill count explodes — engine must compress: cap drills and fold remaining sub-niches into one combined drill rather than 40 questions); free-text during a chip turn (both captured); gibberish free text (one clarifying turn, then park); skip-everything (roots-only profile, feed works); LLM timeout/malformed JSON (typed retry, deterministic fallback wording for TUNE, never a dead-end); re-run (clean replace, no orphaned mutes or follows).
- **Chat onboarding UI (app, replaces `InterviewChat` + absorbs `sources`/`build` stages).** Responsibility: render the one-scrollback chat per the design prototype's interaction contract — multi-select chips with confirm, live composer during sub-niche steps, answered steps collapsing into user bubbles, budget card with ± steppers and pinned total, YouTube tile grid (~30, profile-relevance sorted), X cluster checklist (samples + expandable handle list), YOUR-30 summary card. Must always: keep history visible (no screen swaps); keep the composer functional whenever the contract says typing is allowed; persist only at terminal confirm (no half-profiles); stamp `user_onboarded_at` only at true flow end (2026-06-30 gate rule); hand supply expectations from catalog data, no live calls. Edge cases: mid-flow abandon (resume or restart cleanly, nothing stamped); back-scroll editing an earlier answer (downstream answers invalidated and re-asked); zero YouTube or X picks (valid — slots default to news); budget card remixed to 30/0/0 or 0/0/30 (valid, persists).
- **YouTube reel pipeline (pipeline, extends `youtube.py` + `produce_source_reels.py`).** Responsibility: detect new uploads on followed channels, produce one reel per long-form video. Must always: exclude Shorts; script from the actual transcript (yt-dlp captions primary, audio-transcription fallback); use the video's own thumbnail as the reel image; carry channel credit + tap-through URL; pace RSS fetches (June throttling incident); respect produce-once idempotency. Edge cases: caption-less video (audio fallback, not skip-silently); both transcript paths fail (skip the video loudly, slot falls to news floor); zero uploads across all followed channels (all YT slots → news floor); a video longer than the transcript budget (truncate/summarize, never fabricate); duplicate detection across days (a video produces one reel ever).
- **X cluster sweep + theme engine (pipeline, extends `x_account.py`).** Responsibility: sweep each followed cluster once daily (one batched `x_search` call, ≤ 18 handles), extract per-cluster themes, produce theme-of-the-day reels per user+category via the ladder. Must always: filter to original posts (never retweets); sweep once per cluster per day regardless of follower count; store sweep output + themes shared (not per-user); walk the ladder in order (theme → second theme → roundup → news) and stamp which rung filled the slot; use the top-tweet screenshot as the reel image; attribute the handles/tweets the theme came from. Edge cases: cluster silent today (ladder to news, honest); one loud handle drowning the cluster (theme extraction must require multi-handle support, not one person's thread); xAI API failure or provider shutdown (all X slots → news floor, loud log, adapter seam swappable); tweet screenshot render failure (existing synthetic-poster fallback); two clusters converging on the same theme for one user (one reel, dedupe).
- **Per-anchor news scalpel (pipeline).** Responsibility: turn WHO anchor terms into niche story candidates via BigQuery entity tags (batched) + a single paced GDELT DOC loop. Must always: run DOC sequentially at ≥ 5 s spacing, nightly only; cap per-anchor candidates; tag candidates to the interest node (feeds the shipped niche-first assembly); leave the backbone untouched. Edge cases: DOC penalty box (back off, resume, never parallel-retry); anchor with zero hits (valid — ladder handles it); anchor matching junk (per-anchor cap + existing dedup/importance); DOC outage (BigQuery-only night, logged).
- **Budget & allocation (app + pipeline).** Responsibility: persist the chat's budget-card output into `user_feed_allocation` (news categories + youtube + x rows summing to 30) and honor it at assembly. Must always: pin the total at 30 in the UI; allow 0 on any axis; keep the news floor (unfillable source slots roll to news, existing soft-roll logic); keep Build-your-30 as the later editing surface. Edge cases: user with legacy allocation (rebuild-my-feed replaces it cleanly); allocation summing ≠ 30 rejected at write; all-X allocation on a day X fails entirely (30 news stories, honest labels).
- **Seed catalog v2 (data asset — parsed, schema landed).** Responsibility: seed the v2 root-organised catalog (`scripts/seed_catalog/data/root_catalog_v2.json`, parsed 2026-07-04 from the founder-approved artifact) into prod: **8 roots × 15 sub-niches (120 `interests` depth-1 nodes), 170 YouTube channels, 121 X clusters (~1,055 handles, every cluster ≤ 18, one cluster per sub-niche)**. Supersedes the brief's 71-channel/22-cluster numbers. Must always: verify every handle live before seeding (the artifact's own gaps table mandates it — most handles are new and best-effort); resolve YouTube handles → `UC…` channel ids via Data API `forHandle` (a channel that doesn't resolve is excluded, not guessed); upsert idempotently on natural keys (`interest_slug`, `(content_source_type, external_id)`, `cluster_slug`); keep the no-dup rule (a personality bundles their handles, shown once); carry `topic_tags` for profile-relevance sorting. Edge cases: dead/renamed handle (drop + log in the seed report); sub-niche slug colliding with an existing minted node (converge, don't duplicate); re-run of the seed (updates in place, no member duplication).
- **Source avatars (one-time fetch, owned storage).** Responsibility: give every v2 catalog row a small, permanently-owned avatar — YouTube channel thumbnails and X profile images fetched **once**, downscaled to ~128 px WebP, uploaded to a public `source-avatars` storage bucket, `content_sources.thumbnail_url` repointed at our copy. Never hot-linked, never re-fetched, no update schedule (founder decision: once is enough — it just has to look nice in the pickers). Must always: pace fetches politely; skip-and-log failures (a missing avatar renders as the app's initials fallback, never blocks seeding); be idempotent (rows already pointing at the bucket are skipped). Edge cases: **the X avatar source is the open constraint — live-verified 2026-07-04:** unavatar.io anonymous tier ≈ 25 req/day/IP (useless for ~1,055 handles) and both Twitter syndication endpoints are dead (empty 200 / 429). The slice picks the path: paced unavatar over several days, `pbs.twimg.com` URLs resolved via the xAI adapter, or ship X with initials fallback while YouTube avatars (Data API thumbnails — verified working, 1 quota unit/channel against 10k/day) land immediately.

### Milestones (coarse — slices come from /to-issues)

- **M1 — YouTube transcript live-test (the spike, first).** True when: yt-dlp caption extraction is exercised **from deployed infra** (Railway or the batch runner) against ≥ 10 real videos across ≥ 5 followed channels, the block/success rate is recorded, the audio-transcription fallback is proven on ≥ 2 caption-blocked videos, and a go/no-go on the primary path is written down. **This is the riskiest-assumption gate — nothing else in the YouTube half is promised until it passes.**
- **M2 — Chat onboarding end-to-end.** True when: a new user completes the full 5-phase chat (INTERESTS multi-select + WHO drills, TUNE with ANGLE/SKIP + budget card, YOUTUBE grid, X CLUSTERS, YOUR 30) on the phone; profile + mutes + allocation + source/cluster follows persist; skip fast-forward works; skip-everything still onboards; rebuild-my-feed runs the new chat with clean replace.
- **M3 — YouTube reels in the daily 30.** True when: followed channels' new long-form videos appear as transcript-scripted, thumbnail-postered reels with channel credit in the founder's feed via the daily batch, Shorts excluded, unfillable slots rolling to news.
- **M4 — X theme-of-the-day reels.** True when: followed clusters are swept once daily, per-category theme reels appear with the ladder honestly stamped, original-posts-only holds, tweet-screenshot images render, and a silent-cluster day degrades to news visibly.
- **M5 — Catalog + validation.** True when: the v2 seed catalog (120 sub-niche nodes / 170 channels / 121 clusters, handles live-verified) is in prod with owned small avatars on the rows that could get one; supply expectations show at selection time; the founder's real account has run the new onboarding and lived on the feed ≥ 3 days; the 90-day metric is instrumented (slots-filled-without-fallback count + same-day niche representation + interview completion) and a go/no-go is recorded. *(Head start already landed 2026-07-04: catalog parsed to `root_catalog_v2.json`; migrations 0022 + 0028 applied to prod — `source_clusters`/`source_cluster_members` exist with `cluster_subniche` + `cluster_description`.)* The catalog work naturally splits into two slices: **(a) seed the data** (interests + channels + handles + clusters, with live verification) and **(b) the one-time avatar fetch → `source-avatars` bucket**; (b) depends on (a) but nothing else depends on (b).

### Riskiest assumption + de-risk

**That the YouTube transcript pipe works reliably from our infrastructure** — YouTube blocks cloud-IP caption fetching unpredictably. Everything else is live-verified (GDELT DOC, xAI batching) or in production. De-risked at **M1, before any other build**: if captions fail from cloud IPs, the audio-download + own-transcription fallback becomes the primary plan — slower and slightly costlier, but proven before the YouTube half is promised. Secondary risk: xAI dependency (predecessor was shut off mid-flight in June) — mitigated by the swappable adapter seam and the always-on news floor.

## User Stories

1. As a new user, I want onboarding to be one continuous chat conversation, so that setting up feels like talking, not form-filling.
2. As a new user, I want to select **multiple** categories at once ("AI *and* geopolitics *and* markets"), so that my actual mix is expressible.
3. As a new user, I want to multi-select sub-niches within each category, so that one tap-path doesn't force one interest.
4. As a new user, I want the composer available while sub-niche chips are up, so that I can type an interest the bubbles missed without hunting for a "type it" option.
5. As a new user, I want one open question per sub-niche I picked ("Cricket — a series, a player? Name it"), so that the app learns the *who*, not just the topic.
6. As a new user, I want "Nothing specific →" on every drill, so that depth is optional.
7. As a new user, I want my answered steps to collapse into my own chat bubbles with the history visible, so that I can see what I've said and scroll back.
8. As a new user, I want 3–4 quick follow-ons per category that react to my picks, so that tuning feels conversational, not like a settings page.
9. As a new user, I want an ANGLE question per category (which lens grabs me), so that story selection matches how I read.
10. As a new user, I want a SKIP question per category (things to mute), so that topics I'm sick of stay out of my 30.
11. As a user, I want my mutes applied as hard filters at feed assembly, so that "mute" means gone, not demoted.
12. As a new user, I want a story-budget card in the chat with ± steppers and the total pinned, so that I control the news split across my categories.
13. As a new user, I want the default 30 to be 20 news / 7 YouTube / 3 X, so that sources are in from day one without me doing math.
14. As a user, I want to re-mix the 30 all the way to all-news or all-X, so that defaults never constrain me.
15. As a new user, I want ~30 YouTube channel tiles sorted by relevance to my picks, multi-selectable in the chat, so that adding my channels takes seconds.
16. As a new user, I want X handle clusters grouped by my categories with samples and an expandable handle list, so that I can follow a whole scene in one tap.
17. As a new user, I want a "beyond your picks" cluster section, so that I can grab something outside my profile.
18. As a new user, I want a YOUR-30 summary card (news / YouTube / X split) before "Build my 30 →", so that I confirm what I'm getting.
19. As a new user, I want supply expectations at selection time ("these 12 channels ≈ ~5 long-form videos/day"), so that my slot counts stay grounded in reality.
20. As a user in a hurry, I want every question skippable, so that I'm never stuck.
21. As a user skip-spamming, I want consecutive skips to fast-forward (skip a category, then offer "just build my feed"), so that the exit is always near.
22. As a user who skipped questions, I want them recorded for later resurfacing in-app, so that my profile can finish filling in over time.
23. As a user who skips everything, I want a working broad-category feed anyway, so that skipping isn't punished.
24. As an existing user, I want rebuild-my-feed to run the new chat with clean-replace semantics, so that I can upgrade without residue from my old profile.
25. As a user mid-onboarding who quits, I want resume-or-restart on next open with nothing half-stamped, so that abandonment doesn't strand me.
26. As a YouTube follower, I want one reel per new long-form video from my channels — scripted from the transcript, wearing the video's own thumbnail — so that my subscriptions live inside my 30.
27. As a YouTube follower, I want Shorts excluded, so that my slots aren't spent on 40-second clips.
28. As a creator-respecting user, I want prominent channel credit and tap-through to the video on every YouTube reel, so that the reel is a doorway, not a substitute.
29. As an X follower, I want my first X slot per category to be the theme-of-the-day — the story my followed cluster is all reacting to — so that I get what my corner of X is saying without opening X.
30. As an X follower on a quiet day, I want the ladder (second theme → roundup-of-takes → news) with the rung visible, so that quiet days are honest instead of padded.
31. As an X follower, I want original posts only (never retweets) feeding themes, so that the signal is my people's own voices.
32. As a user, I want the v1 theme-reel image to be a screenshot of the top tweet, so that the source is visible at a glance.
33. As a user, I want any unfillable source slot to fall back to news — never padded, never faked — so that my 30 is always real.
34. As a niche follower, I want at least one story for my niche on days real news exists, so that the interview's promise is kept daily (representation, not domination).
35. As the pipeline, I want each X cluster swept exactly once daily in one batched call (≤ 18 handles), shared across all followers, so that cost stays flat as users grow.
36. As the pipeline, I want per-anchor GDELT DOC queries in a single paced nightly loop (1 req/5 s, never parallel), so that we never hit the penalty box.
37. As the operator, I want the transcript live-test result (block rate, fallback proof, go/no-go) written down before the YouTube pipeline is built, so that the weakest link is tested first.
38. As the operator, I want the xAI adapter swappable behind a seam, so that a provider shutdown degrades to the news floor, not an outage.
39. As the operator, I want every new catalog handle live-verified before seeding (the v2 expansion is ~5× and best-effort), so that onboarding never offers a dead handle.
39b. As a new user, I want channel tiles and cluster handle lists to show the real channel art and profile photos (small, fetched once, stored by us), so that the pickers look like the services I already use — with an initials fallback when no image exists.
40. As the operator, I want slots-filled-without-fallback, same-day niche representation, and interview completion instrumented, so that the 90-day metric is measurable from day one.
41. As the operator, I want every pipeline stage logging structured JSON with fix_suggestions, so that failures are debuggable.

## Implementation Decisions

- **Engine extension, not replacement**: new turn kinds ride the existing `POST /api/interview/turn` stateless protocol and HTTP-200 typed-retry contract; terminal schema grows mutes + angles + deferred-skips alongside the shipped micro-interest list. Guardrail constants stay code-enforced in `agents/interview/constants.py`-style, never model-enforced.
- **Onboarding stage machine**: the chat absorbs `interview` + `sources` + `build` into one stage; `loading` and the gate rule (`user_onboarded_at` at true end only) unchanged. The M6a grid screen and Build-your-30 remain as *later* in-app surfaces; onboarding stops offering them as separate steps.
- **New tables**: `source_clusters` + `source_cluster_members` (per the taxonomy doc's schema), `user_mute_terms` (user, category, term), cluster sweep/theme output stored shared (not per-user). Cluster follow expands to `user_content_sources` rows so all downstream source machinery works unchanged, plus the cluster ref for sweep scheduling and theme attribution.
- **Allocation**: `user_feed_allocation` already supports youtube/x enum rows summing to 30 — the budget card writes it; no schema change for the split. Catalog channels gain a cadence-estimate field for supply expectations.
- **Feed contract twin rule holds**: any `daily_feeds` metadata change (theme rung, channel credit, tap-through URL) lands in `src/types/feed.ts` and the Python assembly models together.
- **Theme reels are a reel format, not a category**: they fill the user's `x` allocation slots; ladder rung stamped in row metadata for the honest UI label (same pattern as the shipped niche fallback labels).
- **Deferred-skip records** are written at terminal persist; the in-app resurfacing UI is a fast-follow (out of MVP) but the data contract ships now so nothing is lost.
- **Catalog v2 state already landed (2026-07-04, this session):** migrations **0022** (source_clusters + source_cluster_members) and **0028** (`cluster_subniche`, `cluster_description`) are **applied + recorded on prod**; the parsed catalog is committed at `scripts/seed_catalog/data/root_catalog_v2.json`. Existing machinery to reuse in the seed slices: `scripts/seed_catalog/seed_via_pooler.py` (asyncpg upsert path — REST host can be IPv4-unreachable), `scripts/seed_catalog/youtube_resolve.py` (`forHandle` → id/title/thumbnail/subscribers, key live in `.env`), `scripts/seed_catalog/x_resolve.py` (unavatar probe — but see the ~25 req/day cap in the avatar module contract). Storage API verified reachable; buckets `digest-audio`/`story-posters`/`entity-reference-images` exist, `source-avatars` to be created by the avatar slice. X `content_sources` convention: `external_id` = handle, existing rows hot-link unavatar (the avatar slice replaces hot-links with owned copies).
- **Design tokens**: the chat UI follows `blip-design-guide.md` (source of truth) with the design handoff as the interaction reference; where the handoff HTML conflicts with the guide, the guide wins.

## Testing Decisions

Test external behavior, not implementation (Rule 9). Mock at boundaries: Gemini client, xAI HTTP, yt-dlp invocation, BigQuery, Supabase. Prior art to mirror: `tests/lib/onboarding/` flow tests, interview engine contract tests (slice #1), `agents.pipeline.sim` for assembly.

- **Engine**: contract tests per turn kind — multi-select turns accept multiple taps; exactly one WHO drill per selected sub-niche; ANGLE + SKIP always present per category (the *founder's locked jobs* — a test that passes without SKIP is wrong); skip fast-forward table-driven (skip → category-skip offer → build-my-feed offer); terminal validation unchanged; malformed LLM JSON → typed retry.
- **YouTube pipeline**: long-form filter (Shorts excluded); transcript-primary/audio-fallback/skip-loudly ladder; one-reel-per-video-ever idempotency; thumbnail + credit + tap-through present on every produced reel.
- **X pipeline**: original-only filter; once-per-cluster-per-day sweep scheduling; ladder order with rung stamping; multi-handle theme support requirement (one loud thread ≠ a theme); provider-failure → news floor.
- **Assembly/budget**: 30-sum invariant; 0-on-any-axis allocations; unfillable source slots roll to news with honest metadata; mutes filter hard.
- **Live E2E residual** per milestone named in its DoD (real transcript fetch from deployed infra at M1, real cluster sweep at M4, founder's real feed at M5) — never silently skipped (Rule 12).

## Out of Scope

- Composite multi-element theme images (v2 — v1 is the top-tweet screenshot).
- Vertical sports/topic APIs (GDELT-only in v1; revisit only on persistent census misses).
- In-app resurfacing UI for skipped questions (data contract ships; UI is a fast-follow).
- X sweeps feeding the sub-niche news guarantee (decision 9 — revisit after M5).
- Health as a 9th taxonomy root (decision 2 — revisit on demand evidence).
- Cookie/OAuth subscription import (killed — App Store 5.2.2 + platform ToS).
- Reel/audio/Q&A/voice/article layers, auth, iOS shell — unchanged.

## Further Notes

- **Deviation from /cto step 0.4**: full `/improve-architecture` was skipped in favor of a targeted recon (same precedent as the 2026-07-03 PRD): the interview engine, source adapters, catalog, allocation, and onboarding flow were read directly; every touched module is covered above. Run `/improve-architecture` separately if a broad deepening pass is wanted.
- Existing open issues #10/#11/#12 (persona validation, census, Build-My-30 niche sections) predate this PRD but remain valid — they validate machinery this PRD depends on (decision 9's news guarantee).
- Seed catalog source artifact (v2, canonical): https://claude.ai/code/artifact/bd94cf30-abbb-4e2e-b665-bd3e99d7e67f — parsed into `scripts/seed_catalog/data/root_catalog_v2.json` (8 roots × 15 sub-niches, 170 channels, 121 clusters / ~1,055 handles, all ≤ 18). The artifact's gaps table warns the handle expansion is best-effort — live verification is part of the seed slice, not optional.
- The 90-day metric (≥ 25/30 slots without fallback; same-day niche representation; founder interview completion) was inferred and founder-approved-by-motion — sharpen at the next /office-hours.
- Update `documents/feed-source-revamp-plan.md` supersession note: done alongside this PRD.
