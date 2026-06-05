# Phase 4 (M4): Go-live wiring + Railway deploy + TestFlight

**Milestone:** M4 — App Store ship (this plan = go-live + TestFlight; full App Store submission deferred)
**Status:** Not started
**Created:** 2026-06-02 (from the e2e code-vs-plan audit)
**Owner decisions (2026-06-02):** Live-first then TestFlight · host on **Railway** · enrichment + daily batch **full scale, approved** · Apple Developer **not enrolled yet**.

## Why this plan exists

The e2e audit found every *shipped* feature works (tests 220 TS / 233 PY green; live Supabase 0001–0005 applied + seeded; worker Q&A grounded + refusal + cache + voice-token all verified live locally). But the app a user would actually open is **not wired to any of it**:

1. The reel reads **fixtures**, not Supabase (`src/components/reel/Reel.tsx:49` imports `getFeed` from `fixtureFeed`).
2. **No auth gate** on `/` — the app is anonymous fixtures.
3. The **worker is undeployed** — `next.config.ts` is `output:"export"` (no API routes), so Q&A + voice need a public FastAPI worker; without it those features 404.
4. **Detail enrichment is OFF** (`enable_detail_enrichment=False`) → `story_analytics` + `detail_key_points` are empty; the 2c "second-analytic" tab + 5-bullet block render blank.
5. The **daily pipeline is undeployed** (Trigger.dev cron unregistered) → `daily_feeds` never populates → no real personalization.

Wrapping today's SPA in Capacitor would ship a hollow demo. So: wire live → deploy → *then* iOS.

## Critical path & Day-0 parallelism

**Apple enrollment is the long pole (~24–48h approval) and is NOT started — kick it off first, it runs in the background while everything else proceeds.**

```
Day 0 (start in parallel):
  ├─ 4c-SP0  Apple Developer enrollment  ........ (~24–48h, unattended)
  ├─ 4a-SP1  Deploy worker to Railway  ......... (gives the public worker URL 4b needs)
  └─ 4c-SP1  Capacitor scaffold (no signing)  .. (buildable shell, simulator-only)

Then (needs worker URL):
  ├─ 4a-SP2  CORS + rate-limit the public worker
  ├─ 4b-SP1..4  Wire SPA → live (feed swap, auth gate, point Q&A/voice at worker, bug fixes)
  └─ 4a-SP3..4  Enrichment ON + full batch + deploy daily cron  (populates analytics + daily_feeds)

When enrollment clears AND 4b is done:
  └─ 4c-SP2..4  Signing → simulator+device smoke → TestFlight
```

Dependency summary: **4b needs 4a-SP1** (worker URL). **4c-SP2+ needs enrollment + 4b** (a real, live-wired build to ship). 4a-SP3/4 (enrichment/cron) should land before TestFlight so testers see real personalized analytics, but don't block the build.

---

## Phase 4a — Deploy the worker + go-live data (Railway)

**Goal:** a public HTTPS FastAPI worker serving Q&A + voice-token, hardened for public use, plus the daily pipeline running so `story_analytics`/`detail_key_points`/`daily_feeds` are real.

### SP1 — Dockerize + deploy the worker to Railway
- **Files:** `Dockerfile` (python:3.12-slim, `uvicorn agents.worker.main:app`), `.dockerignore`, `railway.json`/service config; `.env.example` += worker vars.
- **Ships:** `https://<worker>.railway.app/api/story/{id}/question` + `/api/voice/live-token` reachable publicly. Env set on Railway: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY` (never in the image — runtime env only, per CLAUDE.md).
- **DoD:** `curl` the deployed `/api/story/s1/question` returns HTTP 200 grounded answer (same shape verified locally 2026-06-02); `/api/voice/live-token` returns an `auth_tokens/…` token; logs show structured JSON, no secret values.
- **Deps:** none (live Supabase already up).

### SP2 — Public-hardening: CORS + rate-limit (the 2 open CSO follow-ups)
- **Files:** `agents/worker/main.py` (FastAPI CORS middleware scoped to the app origin — NOT `*`; a rate-limiter on the paid Q&A route). Ref: `.agents/cso-findings/phase-2b-2c-m2.md`.
- **Ships:** the paid Q&A endpoint can't be hammered anonymously; only the app origin (+ Capacitor `capacitor://localhost` / the dev origin) may call it.
- **DoD:** a cross-origin request from a disallowed origin is rejected; >N req/window from one client is throttled (429); allowed origin still 200s. Unit/integration test for both.
- **Deps:** SP1.

### SP3 — Flip enrichment ON + wire `daily_batch.py` + full-scale batch run
- **Files:** `agents/pipeline/daily_batch.py` (build `interest_segment_lookup`, call `load_outlets_lookup`, share ingestion's `GdeltDocAdapter`, pass `enable_detail_enrichment=True`); orchestrator default flip is **not** global — wire it through the batch entry only.
- **Ships:** a real batch run that ingests live news, produces digests, **and** enriches detail (GDELT coverage census + grounded key-figure/timeline/second-analytic/5-bullets, numbers grounded-or-omitted), writing `story_analytics` + `detail_key_points` + `story_trust` + per-user `daily_feeds`.
- **DoD:** after the run, live `story_analytics` and `detail_key_points` are non-empty for produced stories; `fetchStoryDetail(story_id)` returns every 2c field for a `partisan` story and a `reach` story; ≥2 users with distinct `daily_feeds`. **Cost:** paid Gemini (approved full-scale) — log actual spend.
- **Deps:** SP1 (DB reachable); migrations 0004/0005 + outlets seed already live.

### SP4 — Deploy/register the Trigger.dev daily cron
- **Files:** `trigger/dailyPipeline.ts` (`schedules.task`), `trigger.config.ts`; the TS→Python seam (`@trigger.dev/python` build extension or an HTTP call into the deployed worker) — pick one, document it.
- **Ships:** the daily batch runs on a schedule in production (not just a local manual run).
- **DoD:** `trigger deploy` succeeds; the scheduled task shows in the Trigger.dev dashboard; a manual trigger of the deployed task completes and writes a fresh day's `daily_feeds`. Pick the daily run hour + the client `feed_date` timezone rule (phase-1c Open Q3).
- **Deps:** SP3.

---

## Phase 4b — Wire the SPA to live (finish phase-1c's web half)

**Goal:** the running web app reads live Supabase, gates on auth, and talks to the deployed worker. This is phase-1c's non-iOS content (SP1, SP4) + the deferred bug fixes; iOS shell mechanics stay in 4c.

### SP1 — Swap reel feed: fixtures → Supabase (with safe fallback)
- **Files:** `src/lib/feed/index.ts` (NEW provider selector), `src/components/reel/Reel.tsx:49` (import from the selector, not `fixtureFeed`). Keep `fixtureFeed` behind a dev flag (don't delete — Rule 3).
- **Ships:** authed user → `getDailyFeed(userId, feedDate)` (per-user `daily_feeds`, phase-1c SP4); empty/no daily_feeds yet → fall back to `getFeed()` (the 5 live-seeded stories) with a "building your feed" state (phase-1c Open Q4); fixtures only in dev.
- **DoD:** `getDailyFeed` + `getFeed` both return `Story[]` shape-identical to `src/types/feed.ts`; existing `tests/lib/feed/supabaseFeed.test.ts` stays green; a `git diff` shows reel/karaoke components functionally untouched (seam held); loading `/` for a seeded user plays their feed from Supabase storage URLs.
- **Deps:** 4a-SP3 (daily_feeds populated) for the per-user path; works on seeded global feed before that.

### SP2 — Root auth/onboarding gate (the gate phase-1e owed 1c)
- **Files:** `src/app/page.tsx` (gate before mounting the reel), `src/lib/auth/routeGuard.ts`, `src/components/AppRouter.tsx`.
- **Ships:** signed-out → email sign-in; authed + not onboarded (`users.user_onboarded_at` null) → interest chips; authed + onboarded → reel. **No flash of the reel** before the gate resolves.
- **DoD:** the three routing cases asserted against a mocked session + `user_onboarded_at`; no reel flash (Rule 12); reel components untouched (`git diff`).
- **Deps:** phase-1e (already shipped: auth + `users` + chips).

### SP3 — Point Q&A + voice at the deployed worker
- **Files:** `.env` / build env `NEXT_PUBLIC_QA_API_BASE_URL` = the Railway worker origin; verify `src/lib/qa/askQuestion.ts` + `src/lib/voice/useGeminiLive.ts` (token mint) read it; `.env.example` documented.
- **Ships:** in-app typed Q&A returns grounded answers/refusals from the live worker; swipe-left Voice mints a token from the live worker and opens the Gemini Live WSS.
- **DoD:** from the running web app, a typed question returns a grounded answer with citations (and an off-source question refuses); Voice mode mints a token + opens the socket. (Live-mic conversation smoke is human-owed.)
- **Deps:** 4a-SP1/SP2 (worker public + CORS allows the app origin).

### SP4 — Fix the two flagged bugs
- **Files:** `src/components/reel/ReelStory.tsx` / `src/lib/reel/useReelAudio.ts` (read `isVoiceOpen` from `useLayerStack()`; **pause reel narration when Voice opens** — currently they overlap); `src/lib/follows.ts` (the eager-default-throw bug SP4-3b flagged, mirror of the `signals.ts` fix).
- **DoD:** opening Voice over a playing story pauses the reel audio (no double-audio); a test covers the pause-on-voice-open; `follows.ts` no longer throws on the default path (regression test). Full vitest stays green.
- **Deps:** SP1.

---

## Phase 4c — Capacitor iOS + TestFlight

**Goal:** a real, live-wired iOS build distributed to TestFlight. (Reuses phase-1c SP2/SP3 mechanics — Capacitor project, Info.plist, safe-areas, audio unlock.)

### SP0 — Apple Developer enrollment  ⚠ START DAY 0
- **Action:** enroll in the Apple Developer Program ($99/yr), create the App ID / bundle identifier, an app record in App Store Connect, and a TestFlight internal testing group.
- **DoD:** membership active; bundle ID + App Store Connect app record exist. **Long pole — kick off immediately; everything else runs in parallel.**
- **Deps:** none. *Owner action (interactive Apple sign-in — you do this; I can't).*

### SP1 — Capacitor scaffold (simulator-buildable, no signing)
- **Files:** `capacitor.config.ts` (`appId`, `webDir:"out"`), `package.json` (`build:ios` = `next build` → `cap sync`), generated `ios/`, `.gitignore` for `ios/App/Pods` + build artifacts. Capacitor 6.x pins per `reference/stack-notes.md`.
- **DoD:** `npm run build` → `npx cap add ios` → `npx cap sync ios` clean; `xcodebuild … -sdk iphonesimulator build` compiles. ⚠ generates a native `ios/` project.
- **Deps:** none (uses the static export; can start Day 0).

### SP2 — Signing + iOS WebView realities
- **Files:** `ios/App/App/Info.plist` (`NSMicrophoneUsageDescription` real copy for Voice; ATS allows Supabase + Railway https), `src/components/PhoneShell.tsx` (real `env(safe-area-inset-*)`), `<audio>` `playsinline` + first-tap unlock, signing/provisioning/team in Xcode.
- **DoD:** app launches in the Simulator showing the live-wired reel; safe-areas correct on an iPhone 15-class sim; first tap unlocks audio; mic permission prompt shows real copy.
- **Deps:** SP0 (signing), SP1, **4b** (live wiring — so the device build isn't fixtures).

### SP3 — On-device smoke (the full M-loop)
- **DoD (manual, flagged):** on a real device — sign in via magic link, pick interests, the reel plays the user's `daily_feeds` back-to-back with synced karaoke captions to All-caught-up; swipe-right Detail renders body + trust + **populated** 2c analytics; typed Q&A grounds/refuses; swipe-left Voice holds a hands-free grounded conversation (and pauses the reel). Magic-link redirect + Supabase/Railway reachable over device network confirmed.
- **Deps:** SP2, 4a-SP3 (real analytics), 4b.

### SP4 — TestFlight upload
- **Files:** archive build, `ios/App/ExportOptions.plist`, App Store Connect metadata for TestFlight (not full review).
- **DoD:** an archived build uploaded to App Store Connect; available to the internal TestFlight group; at least one tester installs and runs the loop. **Full App Store *review* submission deferred** (needs privacy policy, screenshots, accuracy/guardrail review — see backlog).
- **Deps:** SP3.

---

## Cleanup backlog (non-blocking — fold into a sub-phase when convenient)
- **Custom interest-node creation** is deferred (RLS blocks client `interests` insert) — needs a service-role endpoint or migration. Onboarding custom chips won't persist novel nodes until then.
- **`story_qa` cache loses per-citation `source_url`/`passage_id`** on cache hit (no schema column) — a later migration adds the columns if cached-answer citations need to deep-link.
- **`feed_utils.py` (RSS adapter)** deferred until an RSS source lands (Rule 2 — would be dead code now).
- Delete the leftover fixture row `FIXTURE-SP3-950c5e0f05a1` if still in Supabase.

## Deferred to a later plan (full App Store ship)
Privacy policy + App Privacy "data safety" answers, App Store screenshots/preview, marketing copy, the **accuracy/guardrail review** (master-plan M4 — news has zero hallucination tolerance), and the 3x/week habit metric instrumentation. TestFlight (this plan) is the gate before that.

## Risks / honest flags
- **Cost:** 4a-SP3 full-scale batch is real paid Gemini spend (approved) — log actuals; a runaway ingest could surprise. Cap the first run if uneasy.
- **iOS network:** magic-link redirect URL + Supabase/Railway must be reachable + ATS-allowed from the device; the `NEXT_PUBLIC_APP_URL` / Supabase redirect allowlist must include the Capacitor scheme.
- **Q&A latency on device:** the grounded round-trip was ~29s locally (real Gemini + verification). Confirm the in-app loading UX tolerates that; cache hits are ~instant.
- **Voice live-mic** end-to-end is the one thing no automated test covers — must be smoked on-device (4c-SP3).
