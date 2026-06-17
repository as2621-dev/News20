# Phase 5d: Ingestion of followed sources (YouTube + X)

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L

## Goal
A followed **YouTube channel** or **X account**'s fresh content is detected on a
schedule, transcribed/captured, deduped, and promoted into the per-user **story
pool** that feeds the existing digest pipeline — so source-driven reels sit
alongside topic-driven news and the `youtube`/`x` feed categories stop
soft-rolling into topics.

## Locked decisions (owner, 2026-06-17)
- **Scope = YouTube + X only.** Podcast ingestion is explicitly deferred (see Out of scope).
- **YouTube upload detection = channel RSS** (`https://www.youtube.com/feeds/videos.xml?channel_id=<id>`):
  keyless, returns latest ~15 videos + `media:thumbnail`. **No YouTube Data API key.**
- **YouTube transcript = `yt-dlp`** (auto-subs / uploaded captions, `--skip-download
  --write-auto-sub --write-sub --sub-format vtt`). Caption-less videos → `failed` (skipped).
- **X content = xAI / Grok API** (`XAI_API_KEY`, already in `.env`) with Live Search
  to surface recent substantive posts from a followed handle + their tweet URLs.
- **Images — NO generated posters for source items:**
  - **YouTube → the video thumbnail** (`maxresdefault`/`hqdefault` from RSS or yt-dlp metadata).
  - **X → a screenshot of the tweet**, rendered by **Playwright headless Chromium**
    loading the X embed/publish widget for the tweet URL → PNG.
  - The poster stage **skips Nano Banana Pro** when a story is source-origin and
    carries a supplied image; that image becomes the reel poster directly.

## Why this phase exists
The sources axis is inert until followed sources actually produce digest content
(spec §4). This phase builds the two adapters the owner named, reusing the TL;DW
donor's fresh-upload trick for YouTube, and wires source content into News20's
existing `agents/ingestion` + `agents/pipeline` so produced reels are
indistinguishable downstream — except their image is the real thumbnail/tweet,
not a generated poster.

## Context the sub-agents need
- **Adapter contract:** `agents/ingestion/adapters/base.py` — two phases:
  `search(query, since) -> list[CandidateStory]` and `extract_body(candidate) ->
  CandidateStory`. Source adapters are *source-keyed* not query-keyed, so they add
  a `fetch_new_items(source_external_id, since) -> list[CandidateStory]` method
  (the `source_pipeline` calls that, not `search`); `extract_body` still fills
  `candidate_body_text` (transcript / tweet text). Emit `CandidateStory` with
  `candidate_social_image_url` = thumbnail (YT) or rendered-screenshot path (X),
  `candidate_outlet_domain` = `youtube.com` / `x.com`, `candidate_outlet_name` =
  channel / `@handle`, `candidate_external_id` = video / tweet URL.
- **Donor:** `reference/sources-reuse-map.md` §3–§4 — `adapters/youtube.py:62-477`
  for the uploads-detection shape (but we use **RSS + yt-dlp**, not the Data API),
  `scheduler.py` `CadenceScheduler`, `trigger/ingestion-cron.ts` fan-out pattern.
  **Drop Pinecone** (in-context grounding — `news20-qa-incontext-grounding`).
- **Catalog:** followed sources live in `content_sources` (cols incl.
  `content_source_type`, `external_id`, `source_name`, `thumbnail_url`,
  `platform_metadata`) + the user→source follow table from Phase 5b/5c. RSS needs
  the channel id in `external_id`; X needs the handle.
- **Poster seam:** the produce pipeline's poster stage (`agents/pipeline` /
  `agents/m0/build_poster_from_news.py`, model `gemini-3-pro-image-preview`) must
  branch: source-origin candidate with a supplied image → use it, skip generation.
- **Secrets:** `XAI_API_KEY` (present). No `YOUTUBE_API_KEY`/`OPENAI_API_KEY` needed
  (RSS + captions, no Whisper). Via `agents/shared/settings.py`, never hardcoded/logged.
- **Server-side only** — runs in the Python worker / Trigger.dev, never on device.
- **New deps:** `yt-dlp` (transcript + metadata), `playwright` + chromium (tweet
  screenshot). Add to `requirements.txt`; the Railway worker image must install the
  Playwright browser (`playwright install chromium`) — flag for deploy.

## Sub-phases

### Sub-phase 1: YouTube adapter (RSS detect + yt-dlp transcript + thumbnail)
- **Files touched:** `agents/ingestion/adapters/youtube.py`, `agents/ingestion/adapters/__init__.py`, `agents/shared/settings.py`, `requirements.txt`.
- **What ships:** an adapter that, given a channel `external_id` + `since` cutoff,
  reads the channel RSS feed, keeps videos published after the cutoff, and for each
  uses `yt-dlp` to pull the transcript (auto-subs/captions) + canonical metadata,
  emitting `CandidateStory` records with `candidate_social_image_url` = the video
  thumbnail and `candidate_body_text` = the transcript. Caption-less videos are
  returned `failed` and skipped (never crash the batch).
- **Definition of done:** given a fixture RSS feed + cutoff, returns new-video
  `CandidateStory` items with transcript + thumbnail (RSS fetch + `yt-dlp` **mocked**);
  a caption-less video is skipped as `failed`; conforms to the base interface. Pytest, externals mocked.
- **Dependencies:** Phase 5b/5c (follow tables).

### Sub-phase 2: X adapter (xAI discovery + Playwright tweet screenshot)
- **Files touched:** `agents/ingestion/adapters/x_account.py`, `agents/ingestion/tweet_screenshot.py` (new), `agents/ingestion/models.py` (add `TwitterContentMetadata`), `agents/shared/settings.py`, `requirements.txt`.
- **What ships:** an adapter that, given a handle + cutoff, calls the **xAI/Grok API**
  (Live Search) for recent substantive posts from that handle, normalizes each to a
  `CandidateStory` (`candidate_body_text` = tweet text, `platform_metadata` =
  `TwitterContentMetadata`: tweet id, author, quote/thread refs), and renders a
  **tweet screenshot** via `tweet_screenshot.py` (Playwright headless Chromium loads
  the X embed for the tweet URL → PNG saved to the assets dir), setting
  `candidate_social_image_url` to it.
- **Definition of done:** given a handle + cutoff, returns recent posts as
  `CandidateStory` records (xAI **mocked**); the screenshot renderer produces a PNG
  for a tweet URL (Playwright **mocked**/stubbed in tests); on rate-limit/no-auth the
  adapter returns a clean `failed` + structured error with `fix_suggestion` (no crash,
  no secret leak). Pytest mocked.
- **Dependencies:** Phase 5b/5c.

### Sub-phase 3: Source pipeline + cadence + promote-to-pool + poster-skip
- **Files touched:** `agents/ingestion/source_pipeline.py` (new), `agents/ingestion/scheduler.py` (new — `CadenceScheduler`), `agents/ingestion/dedup.py` (extend), `agents/pipeline/produce_gate.py` (accept source-origin candidates), poster stage (branch on source image).
- **What ships:** `run_source_ingestion(user)` — load the user's active followed
  sources → cadence-filter (YouTube 6h / X 6h, configurable) → dispatch adapter →
  dedup → **promote** substantive items into the per-user deduped story pool tagged
  to the user (so `produce_gate`/`orchestrator` treat them as candidates like news);
  AND the poster-stage branch that uses `candidate_social_image_url` for
  source-origin stories instead of generating a Nano Banana poster.
- **Definition of done:** a followed channel's new upload becomes a story-pool
  candidate tagged to the user, flows through `produce_gate`, and its reel uses the
  thumbnail (no poster generation) — adapters + poster client **mocked**; cadence
  blocks re-fetch within the window; dedup drops an already-ingested item. Pytest, mocked.
- **Dependencies:** SP1, SP2.

### Sub-phase 4: Trigger.dev source-ingestion cron
- **Files touched:** `trigger/sourceIngestion.ts` (new), `trigger.config.ts` (if needed).
- **What ships:** a Trigger.dev **v4 `schedules.task`** (`cron: "0 */2 * * *"`) that
  lists users with ≥1 active source and fans out `run_source_ingestion` per user
  (port `ingestion-cron.ts`), with structured per-run logging of items
  fetched/promoted/dropped (no silent caps).
- **Definition of done:** valid v4 `schedules.task` (uses `@trigger.dev/sdk` v4, **never**
  `client.defineJob`); a dev trigger fans out per-user ingestion and writes promoted
  candidates (test DB / mocked worker call); cron expression validated; run logs counts.
  ⚠ live = outward API calls — keep gated to test/dev until M5 deploy.
- **Dependencies:** SP3.

## Phase-level definition of done
On a schedule, each user's followed YouTube/X sources are polled by cadence, fresh
content is fetched (RSS+yt-dlp captions for YT, xAI posts + Playwright screenshot
for X), deduped, and promoted into the per-user story pool feeding the existing
produce pipeline — with the reel image being the **video thumbnail / tweet
screenshot**, not a generated poster. **Validated by:** the two adapter tests
(mocked APIs, incl. caption-less + rate-limit failure paths); the screenshot
renderer test; the source-pipeline cadence + dedup + promote + poster-skip test;
the v4 cron validity + fan-out test.

## Out of scope
- **Podcast ingestion** (RSS + Whisper) — deferred to a later sub-phase per owner.
- The **control surface** allocation (Phase 5e) — this fills the pool; 5e decides slot counts.
- The **recommendation/onboarding** UI (Phase 5c).
- Periodic **catalog refresh** / discovery (Phase 6).
- The digest audio/caption pipeline — reuses existing `agents/pipeline` unchanged.

## Open questions
1. **xAI Live Search fidelity** — does Grok reliably return a specific handle's
   recent posts + canonical tweet URLs? If not, fall back to fetching the handle's
   syndication timeline for URLs, with xAI only for substance scoring.
2. **Promotion criterion** — what makes a source item "substantive" (transcript
   length / engagement / topic match) — ties to 5e's "Only their big stuff".
3. **Playwright on Railway** — confirm the worker image can run headless Chromium
   (`playwright install chromium` + system deps); if too heavy, fall back to a
   hosted screenshot API.
4. **Dedup across axes** — a story covered by both a followed source and topic news:
   dedupe to the source-origin (Decision #11 pinned-first); confirm here.

## Self-critique
**Product lens:** PASS — delivers spec §4; following the right creators surfaces
their content automatically, and the reel shows the real thumbnail/tweet (higher
trust + recognizability than a synthetic poster).
**Engineering lens:** PASS — conforms to the existing adapter contract (Rule 8),
reuses the donor's upload-detection shape but with a keyless RSS path, drops
Pinecone, and isolates the poster-skip to one branch. DoDs are pytest-verifiable
with mocked externals. The v4 `schedules.task` constraint is honored.
**Risk lens:** PASS with flags. New runtime deps (`yt-dlp`, Playwright/chromium) are
operational weight on the worker image — flagged for deploy. ⚠ SP4 cron makes
outward API calls when live — gated until deploy. Within-phase file overlap is
sub-phase-isolated. Failure paths (caption-less video, X rate-limit, screenshot
render failure) are tested, not swallowed (Rule 9/12).
**Irreversible sub-phases:** none (additive code; the live cron is reversible — disable the schedule).
