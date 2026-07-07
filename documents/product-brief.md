# Product Brief — blip Pre-Launch Hardening

**Date:** 2026-07-06
**Status:** Draft — needs `/cto` to translate into a PRD
**Scope:** Final hardening pass before public launch. Supersedes the 2026-07-04 brief (chat onboarding + YT/X source reels — shipped); prior briefs in git history.

## One-liner
Make blip's core promise real before public launch: top news per followed sub-niche, correctly labeled, in a provably current app.

## Target user
Founder (ashesh) as proxy for launch users: opens blip each morning expecting the 30 reels to lead with the stories leading outlets are covering for his followed sub-niches (tech, geopolitics, cricket…).

## Problem
The daily feed reads as random low-weight news, mislabeled by category; the phone runs stale builds so fixes are invisible; voice mode has ~5-6s dead air while falsely showing LISTENING; poster images burn credits during testing.

## Root causes (from 4-agent code review, 2026-07-06)
1. **Importance engine gated OFF** — `ENABLE_SEMANTIC_CLUSTERING` defaults off, so authority-weighted importance never reaches the ranker; intra-section ranking collapses to recency (`agents/pipeline/stages/ranking.py:493-556`, `agents/pipeline/produce_gate.py:75`). Fixes for it sit uncommitted on `claude/feed-source-revamp-plan-388edf`.
2. **Wrong categories** — GDELT themes missing a thin ~30-code whitelist default to `arts` and OVERRIDE the fetching interest (`agents/pipeline/theme_category.py:42-161`, `agents/ingestion/interest_keyed_pipeline.py:433-449`).
3. **Queryless interests silently skipped** — sections under-fill, padded with backfill that reads as random (`agents/ingestion/interest_keyed_pipeline.py:206-213`).
4. **Stale baked bundle** — `ios/App/App/public/` is gitignored + no version badge; installed binary provably predates current onboarding (build-id mismatch). 5-tab nav is NOT lost (renders after wordmark tap, by design).
5. **Voice lag** — serial setup (token mint on cold Railway → WSS → setupComplete) before any echo is possible; orb shows LISTENING while connecting so first words are dropped (`src/components/blip/reel/AskSheetVoice.tsx:598`, `src/lib/voice/useGeminiLive.ts:523-635`).
6. **Poster gen ungated on worker** — `agents/worker/pipeline_routes.py:467` always creates a live image client; single choke point exists at `agents/pipeline/orchestrator.py` `generate_poster_bytes` (line 333).

## Smallest provable version (launch scope)
1. **Feed importance ON**: commit + enable semantic clustering; "top news" = covered by leading outlets today (authority-weighted). Backfill missing `interest_search_query` rows. Importance floor so backfill never masquerades as followed news.
2. **Category fix**: unknown themes must NOT override the keyword-matched interest's category; expand the theme whitelist.
3. **Build trust**: visible build-stamp (commit/build id) in app; build step that guarantees fresh bundle before archive.
4. **Voice mode**: honest "Connecting…" gate on `isSetupComplete`; pre-warm session at sheet-open; warm token endpoint; PLUS instrument/debug in-conversation turn latency — setup slowness is tolerated, live-conversation slowness is not.
5. **Chat UX (typed + voice)**: correct answers; chat-style layout with latest message at bottom; conversation history persists in-session; voice shows live rolling transcripts of both user and agent. Streaming NOT required — all-at-once answers are fine.
6. **Images OFF**: env-gated kill switch at the orchestrator choke point covering all paths incl. scheduled worker; zero image-model calls until explicitly re-enabled. Posterless reels already degrade gracefully (category-color wash, captions, audio intact).
7. **Onboarding verification**: Playwright pass over all onboarding steps on a FRESH build; fix the ~83px double top-padding gap if it reproduces (`src/components/onboarding/InterviewChat.tsx:453`, `src/components/onboarding/OnboardingFlow.tsx:277-285`).

## 90-day success metric
Launch gate (verified, not vibes): for each followed section, top reels match same-day coverage on leading outlets (spot-check vs BBC/Reuters/Google News top-of-page); 0 mislabeled categories in a 30-reel feed; onboarding→reel→voice→chat all pass end-to-end on a build-stamped fresh install.

## Competition
N/A — hardening pass on existing product. "Do nothing" = launch a feed that visibly fails its core promise.

## What held up under pressure
- "Top news" definition: coverage by leading publications — measurable, and maps 1:1 to the existing authority-weighted importance engine.
- Stale bundle explains both "lost tabs" and (likely) the onboarding misalignment — fix the pipeline, don't chase ghosts.
- Voice lag is architectural (serial setup + no local echo possible) plus a UX lie (LISTENING while connecting).

## What's still soft
- Whether onboarding misalignment reproduces on a fresh build (verify before fixing).
- Live-mode voice turn latency: cause unknown until instrumented (scope item 4 debug task).
- Embedding-credit cost per run with clustering on — assumed acceptable while posters are off (inferred; user did not state a ceiling).
- "Smooth onboarding" acceptance is subjective; Playwright walkthrough + founder eyeball is the bar.

## Riskiest assumption
Enabling semantic clustering (importance + dedup + category-aware merge) actually makes sections lead with the day's top stories — validate the first live run against outlet coverage before calling the feed fixed.

## Contradictions surfaced
- Typed-chat streaming: initially in scope, user cut it — all-at-once answers fine; correctness + chat layout + persistent history are what matter.
- Edge cases explicitly deprioritized; core flows must work end-to-end.

## Open questions
- Approve committing the uncommitted clustering/reconcile work on this branch as part of scope item 1? (Assumed yes.)
- Any budget ceiling for embedding calls per daily run?
