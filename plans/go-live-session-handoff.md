# Go-Live Session Handoff (2026-06-06)

**Read this first.** Continuation note for the App Store go-live push. Spec of record:
`plans/phase-4-m4-go-live-and-testflight.md` (4a/4b/4c sub-phase DoDs). This note
records what actually shipped, the live facts, and the environment gotchas.

## Goal & locked decisions
- **Goal:** ship to the App Store ASAP (owner: "go live today").
- **Reality:** public App Store ≈ **4–7 days** — gated on Apple, not us (enrollment
  ~24–48h + review ~1–3 days). Today's target = deployed + live-wired + testable.
- **Decisions (owner):** Apple enrollment = **not started** (owner doing it — the long
  pole) · **keep Phase 5d in v1** (sources axis) · **TestFlight first**, then App Store.
- **Sources/X:** use **xAI (`XAI_API_KEY`) for X content** — NO Twitter API.

## Status board (mirror of the in-session task list)
| # | Item | Status |
|---|---|---|
| 4a-SP1 | Deploy FastAPI worker to Railway | ✅ DONE + verified |
| 4a-SP2 | CORS + rate-limit worker | ✅ DONE (in code + env-configured) |
| 4a-SP3 | Live batch (enrichment ON) | ⏸️ DEFERRED → runs on Railway cron (4a-SP4) |
| 4a-SP4 | Deploy Trigger.dev daily cron | ⬜ NOT started — **runs the real batch** |
| 4b | Wire SPA to live | 🔄 SP1+SP3 done; **SP2 (auth gate) pending** |
| 4c-SP1 | Capacitor scaffold (simulator) | ⬜ NOT started |
| 5d | Source ingestion (YT/podcast/xAI-X) | ⬜ NOT started (in v1) |
| 4c-SP2..4 | Signing + device + TestFlight | ⛔ BLOCKED on Apple enrollment |
| review | App Store review pack + submit | ⬜ NOT started |

## What shipped this session (verified)
**4a-SP1/SP2 — worker live on Railway**
- **Worker URL:** `https://worker-production-ed3c.up.railway.app`
- Verified: `POST /api/story/s1/question` → 200 grounded answer w/ citations;
  `POST /api/voice/live-token` → 200 ephemeral token.
- CORS + rate-limit already in `agents/worker/main.py:55-132` (env-driven). Set
  `QA_API_ALLOWED_ORIGINS` to the **real app origin** in 4c (currently dev origins).

**4b-SP1 — feed swap (fixtures → live Supabase)**
- NEW `src/lib/feed/index.ts` `getReelFeed()` selector: authed user w/ `daily_feeds`
  → personalized feed; else **global seeded Supabase feed**; `NEXT_PUBLIC_FEED_SOURCE=fixtures`
  → dev fixtures. Per-user path falls back to global until the cron populates `daily_feeds`.
- NEW `getDailyFeed(userId, feedDate)` in `src/lib/feed/supabaseFeed.ts`.
- `BlipReel.tsx` repointed to `getReelFeed`.

**4b-SP3 — Q&A + Voice → live worker** (+ bug fix)
- `.env`: `NEXT_PUBLIC_QA_API_BASE_URL=https://worker-production-ed3c.up.railway.app`.
- **Bug fixed:** `useGeminiLive.ts:412` minted the voice token from a bare relative
  path (`/api/voice/live-token` → 404 on the static build). Now prepends the worker origin.
- Q&A (`askQuestion.ts`) already prepended the base URL — unchanged.

**4b-SP4 — partly moot:** the voice/reel double-audio the M4 plan flagged is already
fixed by the Stage-4 `isOverlayOpen` pause (`BlipReel.tsx:266`). `follows.ts`
eager-default-throw is **latent** (env present → never fires); fix it as polish only.

**Verified:** `tsc --noEmit` clean · 24 feed/reel/voice vitest pass · dev server
hot-reloads · `GET /` → 200.

**4a-SP3 runner (deferred, but built):** `scripts/run_live_batch.py` — live GDELT
ingest + `enable_detail_enrichment=True` + the 3 lookups, guarded (dry-run default,
`MAX_PRODUCE` cap, idempotent demo-user seed, `DNS_PIN` escape hatch). It runs
end-to-end; only **live GDELT ingest** is blocked locally (see gotchas). **$0 spent**
all session (empty-safe pipeline). Reuse `run_daily_pipeline(...enable_detail_enrichment=True)`
from the Railway cron in 4a-SP4.

## Live facts the next session needs
- **Railway:** project **"Blip"**, service **"worker"**, env **production**. `.env` has
  `RAILWAY_TOKEN` + `RAILWAY_PROJECT_ID` (a **project token** — `railway whoami`/`link`
  return Unauthorized; always pass `--service worker`). Deploy: `railway up --service worker --ci`.
  Logs: `railway logs --service worker`. Vars: `railway variables --service worker`.
- **Worker env on Railway:** SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY,
  XAI_API_KEY, SERPER_API_KEY, QA_API_ALLOWED_ORIGINS.
- **Image:** root `Dockerfile` (python:3.12-slim + **ffmpeg** for pydub) + `.dockerignore`
  + `railway.json`. ⚠️ `railway.json` must NOT set `deploy.startCommand` — Railway does
  not shell-expand `$PORT`; the Dockerfile `CMD ["sh","-c","uvicorn ... --port ${PORT:-8000}"]`
  owns startup. (This bit us once — symptom: 502 + `'$PORT' is not a valid integer`.)
- **Demo users seeded in Supabase** (for batch validation): `demo-business-equities-semis@news20.demo`,
  `demo-sport-cricket-india@news20.demo` (each has one `user_interest_profile` row).
- **Dev server:** `PORT=3137 npm run dev` (a stale one may also be on :3100).

## ⚠️ Environment gotchas (these cost time — read before re-running anything)
1. **Local DNS flake on the Supabase host.** This machine's default resolver
   intermittently times out on `cerfennlcgureyifraqy.supabase.co` (google.com etc.
   resolve fine; 8.8.8.8 resolves it → Cloudflare `172.64.149.246`/`104.18.38.10`).
   - Workaround for scripts: `DNS_PIN=cerfennlcgureyifraqy.supabase.co=172.64.149.246`
     (the runner monkeypatches `getaddrinfo`).
   - **Real fix for the owner:** set Mac DNS to **1.1.1.1** (System Settings → Network
     → DNS). This ALSO blocks the **in-browser** reel feed test until fixed.
2. **GDELT rate-limits this IP (HTTP 429).** Hit it too many times today; live ingest
   fails *from this laptop*. Resolves from a fresh IP → that's why the real batch runs
   on **Railway** (4a-SP4). Don't keep probing GDELT. The adapter has NO 429 retry —
   **add a backoff-retry to `agents/ingestion/adapters/gdelt_doc.py` in 4a-SP4**.
3. **Background `Bash` commands have flaky network/DNS here**; foreground is reliable.
   Run network-dependent commands in the foreground.
4. **No passwordless sudo** (can't edit `/etc/hosts`).

## Uncommitted work (MINE — commit by explicit path; concurrent agents share this tree)
```
M  .env.example
M  src/components/blip/reel/BlipReel.tsx
M  src/lib/feed/supabaseFeed.ts
M  src/lib/voice/useGeminiLive.ts
?? .dockerignore
?? Dockerfile
?? railway.json
?? scripts/run_live_batch.py
?? src/lib/feed/index.ts
```
(`.env` also changed — gitignored.) NOT yet committed. Suggested commit split:
`feat(deploy): Railway worker image + 4a-SP1/SP2` and `feat(reel): wire SPA to live
feed + worker (4b-SP1/SP3)`. **Commit only these paths** — other dirty/foreign files
belong to concurrent sessions.

## Next steps (priority order)
1. **(Owner)** Finish Apple Developer enrollment — unblocks the whole iOS tail.
2. **4b-SP2 — auth/onboarding gate on `/`**: signed-out → email sign-in; authed +
   `users.user_onboarded_at` null → interest chips; else → reel. No reel flash.
   Primitives exist: `getCurrentSession()` (`src/lib/supabase/auth.ts`),
   `src/lib/onboardingProfile.ts`, routes `src/app/(auth)/callback`, `src/app/(onboarding)/onboarding`.
3. **4c-SP1 — Capacitor scaffold** (simulator-buildable, no signing): `capacitor.config.ts`
   (`webDir:"out"`), `build:ios` script, generate `ios/`. Pins: `reference/stack-notes.md`.
4. **4a-SP4 — Trigger.dev daily cron** on Railway (runs the real `run_live_batch`
   logic w/ enrichment ON, from a clean IP+DNS). Add the GDELT 429 retry here.
   Verify/apply migration `0010`.
5. **5d — source ingestion** (YouTube + podcast + xAI-for-X) — plan: `plans/phase-5d-source-ingestion.md`.
6. **Apple-gated:** 4c-SP2..4 (signing → device smoke → TestFlight), then the review
   pack (privacy policy, screenshots, accuracy/guardrail review) → submit.

## How to test the app right now (browser)
Open **http://localhost:3137** (set Mac DNS to 1.1.1.1 first if the reel errors).
Reel + karaoke + article + ask-sheet work on the 5 live-seeded stories; typed Q&A +
Voice hit the live Railway worker. No auth gate yet, so it loads straight into the reel.
