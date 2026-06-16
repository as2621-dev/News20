# Go-Live Report — 2026-06-09/10 (dress rehearsal of `/go-live-check`)

## Run metadata
- Git SHA at run: `a2f0eac` (+ uncommitted session changes: Phase-0 fixes, harness, bug fixes A/B, taxonomy seed)
- Worker: `https://worker-production-ed3c.up.railway.app` (token mint verified 200)
- Live story pool: **7 stories** (5 M0 fixtures + 2 GDELT candidates), only **2 interest-tagged** — GDELT ingest stale since 2026-06-06
- API spend: **~$0.46 of the $5.00 cap** (ledger: `.agents/e2e/state/cost-ledger.md`)

## Step × profile matrix (final state, all on fleet-fresh users)

| Step | a-tech-ai | b-sport | c-markets-geo | d-arts-mixed |
|---|---|---|---|---|
| onboarding_splash (session-skip) | PASS | PASS | PASS | PASS |
| picker (real clicks → DB) | PASS (4 entities) | PASS (3 entities) | PASS (3 interests + 1 entity) | PASS (2 interests + 1 entity + 1 custom surfaced) |
| sources (swipe deck) | PASS | PASS | PASS | PASS |
| build_30 | PASS (save) | PASS (save) | PASS (skip path) | PASS (save) |
| reel_loads (fresh-user fallback asserted) | PASS | PASS | PASS | PASS |
| reel_playback (audio clock + karaoke + story advance) | PASS | PASS | PASS | PASS |
| article_layer (live Supabase detail) | PASS | PASS | PASS | PASS |
| text_qa (worker grounding, HTTP 200) | PASS | PASS | PASS | PASS |
| voice_live (token → WS → setupComplete → model responds → teardown) | PASS | PASS | PASS | PASS |
| personalized_feed (second pass) | AMBER¹ | AMBER¹ | **PASS** (reel pos 1 = daily_feeds pos 1) | AMBER¹ |

¹ AMBER = no `daily_feeds` row possible: pool too thin / ranker gap (below), not a journey failure. Evidence: `.agents/e2e/state/<profile>-result.json`, verdicts in `*-verdict.md`, screenshots in `.agents/e2e/state/<profile>/`.

Run notes (honest history): profile-a and profile-b's first fleet attempts had their shells externally killed mid-run (resource contention with 4 concurrent Chromes); both passed fully on targeted-reset reruns. Profiles c and d were intentionally re-onboarded after the mid-session taxonomy seed (below) — both passed twice.

## Evaluator audits (`.agents/e2e/state/evaluator-verdict.md`)
- (a) Ranking invariants: **PASS** (sim all-pass, drift bounded; pytest 9/9, 0 skips)
- (b) Sourcing health: **FAIL** — newest story 89.6h old; 0 stories in last 48h. The GDELT live ingest has produced nothing since 06-06; the sweep effectively ran on fixtures + 2 candidates.
- (c) YouTube/X/podcasts: **GAP** — catalog healthy (3,360 sources across yt/pod/x/people) but `content_source_items` = 0 and 0 stories in any source bucket: **source ingestion not built/run** (5d/Phase-6-cut territory).
- (d) Asset completeness: **PASS** — all 7 stories: audio 200, poster 200, 77/77 caption sentences with word tokens, durations 46–59s.
- (e) Personalization conformance: verified for profile-c (semis stories ↔ markets interests). a/b/d unverifiable (no rows; see gaps).
- (f) RLS: **PASS** — anon/foreign reads blocked, authed cross-user reads blocked, client daily_feeds insert denied.

## Bugs found AND fixed during this run (uncommitted, in working tree)
1. **ArticleLayer pinned to fixture detail** (`ArticleLayer.tsx`) — article body could NEVER load on the live feed. Fixed → Supabase-direct fetch; verified in all 4 journeys.
2. **Gemini Live setup rejected (WS 1007)** (`useGeminiLive.ts`) — `speechConfig` must live inside `generationConfig` (API contract changed); voice hung on LISTENING forever, close swallowed. Fixed + fail-loud `voice_live_setup_rejected`; verified live in 4 voice sessions. Memory updated.
3. **Picker topics never persisted** — `interests` table had 18 rows vs 79 picker topic leaves; ZERO matched, so interest personalization never worked for picker users (RCA: `.agents/e2e/state/topic-persistence-rca.md`). Fixed data-only: `supabase/seed/interests_picker_topics.sql` (79 rows, idempotent, applied to live; 18→97). Verified: c/d re-onboarding persisted 3 and 2 interest rows.
4. **Phase-0 fixes** (pre-run): `NSMicrophoneUsageDescription` in Info.plist (verified in BUILT app); OnboardingFlow splash session-skip (+2 Vitest tests); PhoneShell native-frame drop + onboarding safe-area padding (verified in simulator screenshots).

## Open gaps / readiness list (route to /debug or phases)
1. **GDELT ingest stale** (eval b) — no fresh stories since 06-06. Run/repair the live batch + wire the Trigger.dev cron (4a-SP4 still stubbed). Without this the product has no fresh news.
2. **Ranker ignores entity-only users** (`load_active_user_inputs`: active = ≥1 `user_interest_profile` row) — pure-entity pickers (profiles a, b pattern) get the anonymous feed forever. Widen active-user definition.
3. **Feed-date timezone contract** — `todayFeedDate()` (src/lib/feed/index.ts:24) computes the **UTC** date though its comment says client-local; the allocator/cron writing local dates means US-evening users silently fall back nightly. Reproduced live in this run at 23:52 CDT. Decide one convention.
4. **Story-interest tagging coverage** — only 2/7 pool stories tagged; `feed_matched_interest_id` was null on profile-c's slots (slot kind `breaking`). Personalization can't differentiate beyond a thin pool.
5. **YouTube/X/podcasts ingestion not built** (eval c) — buckets exist in UI + catalog seeded, zero content items/stories.
6. **iOS splash lower-section AMBER** — narrow column + clipped "EMAIL SIGN-IN · NO PASSWORD" microcopy on native (screenshots `.agents/e2e/screenshots/ios-0*.png`). `/debug` candidate.
7. **`"Export controls (chips)"`** picker leaf can't canonicalize (paren-escaping in `findCanonicalInterest`). One-line app fix.
8. Standing items: voice `ask_about_story` tool call verified-by-proxy only; no XCUITest journey on iOS (boot smoke only); magic-link deliverability untested (harness uses password auth); app icons / launch screen / privacy manifest review; offline behavior; Apple Developer enrollment still the TestFlight blocker.

## iOS stage (all PASS)
Build → `BUILD SUCCEEDED`; install/launch exit 0 (PID stable through 20s soak); mic grant applied; mic usage string present in built plist; safe-area wordmark fix verified. Screenshots: `.agents/e2e/screenshots/ios-01-launch.png`, `ios-02-steady.png`. Simulator left OPEN with com.blip.app installed for on-device review.

## Console/network hygiene
2 benign console errors per profile (resource-load noise) + RSC-prefetch `net::ERR_ABORTED` entries and mid-buffer audio aborts; **0 page errors, 0 HTTP ≥400** across all green runs.

## Review pack (manual inspection)
`.agents/e2e/review-pack-2026-06-09/index.md` — per profile: interests selected (clicks + DB truth), stories shortlisted (ranked, scored), every reel (poster embedded, audio link, full ALEX/JORDAN transcript), journey screenshots, and the review-it-yourself recipe. **Profile-c is the one to review for personalization.**

## Cleanup decision
Test-user ROWS were deliberately NOT wiped — profile-c's personalized feed and all onboarding data stay live so you can review them. After manual review, run: `npx tsx scripts/e2e/cleanup-test-users.ts` (rows) or `--purge` (full teardown).

**Next:** review `.agents/e2e/review-pack-2026-06-09/index.md` + the open simulator, then commit the session's work; after that, the highest-value red is gap 1 (stale GDELT ingest) → `/rca` or run the live batch.
