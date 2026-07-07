# PRD — Pre-Launch Hardening

**Date:** 2026-07-06
**Source:** documents/product-brief.md (brainstormed + approved 2026-07-06)
**Status:** Ready for /to-issues

> **Supersession notice.** This PRD supersedes the 2026-07-04 PRD (chat onboarding + YouTube/X source reels — **shipped**: chat onboarding b0e41e3, X theme reels 4d7195b, catalog v2 seed). Its open issues (#10 persona validation, #11 census, #12 Build-My-30 niche sections, #21 avatars-decision, #27 X seed) remain valid backlog. Old PRD recoverable at git `43beae1` and earlier. This is the **final hardening pass before public launch** — no new features; make the shipped promise true.

## Problem Statement

Diagnosed by a 4-agent code review (2026-07-06); all root causes confirmed with file:line evidence:

1. **Feed reads as random low-weight news.** The authority-weighted importance engine exists but is gated OFF (`ENABLE_SEMANTIC_CLUSTERING` defaults off). In the default path importance falls back to raw outlet count (~0 for niche stories) and affinity/depth are constant within a section, so intra-section ranking collapses to **recency** (`agents/pipeline/stages/ranking.py:493-556`, `agents/pipeline/produce_gate.py:75`). Followed interests without `interest_search_query` are **silently skipped** at ingestion (`agents/ingestion/interest_keyed_pipeline.py:206-213`), leaving sections to be padded with backfill.
2. **Wrong categories.** Stories whose GDELT themes miss the ~30-code `THEME_CATEGORY_WHITELIST` default to `arts`, and that depth-0 tag **overrides** the keyword-matched interest that fetched the story (`agents/pipeline/theme_category.py:42-161`, `agents/ingestion/interest_keyed_pipeline.py:433-449`).
3. **Stale builds are invisible.** `ios/App/App/public/` is gitignored, TestFlight ships whatever was last `cap sync`'d, and no build id is shown anywhere — the founder's installed binary provably predates current onboarding (bundle build-id mismatch). The 5-tab nav is NOT lost; it renders after the wordmark tap by design.
4. **Voice mode: ~5-6s dead air + dropped first words.** Serial setup (token mint on cold Railway → WSS → `setupComplete`) runs before any echo is possible, while the orb shows LISTENING during `connecting` (`src/components/blip/reel/AskSheetVoice.tsx:598`, `src/lib/voice/useGeminiLive.ts:523-635`). Typed chat is already optimistic.
5. **Poster credits burn on every scheduled run.** `agents/worker/pipeline_routes.py:467` creates a live image client unconditionally; `POSTER_MODE` only gates `run_live_batch.py`.

## Solution (user's perspective)

- My 30 leads each section with **the story leading outlets are covering today** for that sub-niche, climbing the ladder (leaf → niche → category) only when the leaf is honestly thin.
- Every reel wears the **right category**.
- The app shows a **build stamp**; a stale install is self-evident.
- Voice mode says **"Connecting…" until it's actually listening**, connects fast, and once live, turns are fast. Both chat modes read like chat: latest message at the bottom, history persistent in-session, voice transcripts (mine + agent's) rolling live.
- **No poster images are generated** (cost hold) — reels render the category-color wash; audio/captions unchanged.

## Technical Foundation

### Tech stack

Existing, unchanged, fixed: Next.js 15 static export + Capacitor 8 (Vercel), Python 3.12 FastAPI worker (Railway), Supabase, GDELT BigQuery, Gemini (`google-genai`), Trigger.dev v4. No new components (Rule 2).

### Architecture delta (only what changes)

```
Ingestion ──► [FIX A: loud-fail queryless interests + query backfill]
   │
   ▼
Stage B.5 reconcile (semantic clustering) ── [FIX B: ON by default; commit
   │        branch work: gemini-embedding-001@768d, reconcile.py wiring]
   ▼
cluster_importance ──► ranking ── [FIX C: importance floor; no recency-only
   │                              sections; backfill labeled honestly]
   ▼
theme→category tagging ── [FIX D: unmatched theme NEVER overrides the
   │                       fetching interest's category; widen whitelist]
   ▼
produce ── [FIX E: DISABLE_POSTER_GEN gate at orchestrator.generate_poster_bytes
   │        choke point (line ~333) — covers worker + batch + scripts]
   ▼
app ── [FIX F: build stamp (git SHA) visible in Settings + guaranteed-fresh
        bundle on archive]  [FIX G: voice connect UX + latency instrumentation]
        [FIX H: chat/voice history + rolling transcripts]
```

### Key design decisions

1. **Semantic clustering ON in production.** Commit the uncommitted branch work (embeddings model fix, `reconcile.py`, daily_batch/worker gating) and default `ENABLE_SEMANTIC_CLUSTERING=1` on worker + batch. This is the only path to real importance (authority-weighted, within-category-normalized, same-event-deduped). Embedding cost approved while posters are off. Rules out: hand-tuned outlet-count heuristics.
2. **"Top news" = covered by leading outlets today** (founder's definition, measurable). Acceptance = spot-check per section against BBC/Reuters/Google-News-top for that topic, same day.
3. **Category precedence flips: the fetching interest wins.** A theme-derived category may only override when a whitelisted theme actually matched; unmatched themes must not stamp `arts` at depth-0. Also expand `THEME_CATEGORY_WHITELIST` (quantify misses via `theme_category_no_whitelisted_theme` logs first). Rules out: any silent default category.
4. **Queryless followed interests fail loud.** Skipped interest → warning log with `fix_suggestion` + surfaced count in batch summary; backfill `interest_search_query` for all followable interests (script exists precedent: 2026-07-04 backfill). Rules out: silently empty sections.
5. **Importance floor on section fill.** Below-floor candidates lose to the honest climb/backfill rung rather than filling a leaf slot on freshness alone; ladder rung stamping (shipped) stays the honesty mechanism.
6. **One poster kill switch at the choke point.** `DISABLE_POSTER_GEN=1` forces `poster_genai_client=None` inside `orchestrator.generate_poster_bytes` — one edit covers run_live_batch, worker, produce_source_reels. Batch path closed by never running `fill_batch_posters.py`. Source reels keep supplied-image posters (free). Posterless rendering is already graceful (`ReelStage.tsx:214`). Rules out: gating each of the 3+ client-creation sites.
7. **Build stamp + fresh-bundle guarantee.** Inject `NEXT_PUBLIC_BUILD_ID` (short git SHA + build time) at `next build`; render in Settings. `npm run build:ios` stays the one blessed path (build → sync); add a preflight check that refuses `cap sync` output older than `out/`. Rules out: remote-URL bundle serving (offline-first stays).
8. **Voice: honest states + pre-warm, then measure.** Gate LISTENING on `isSetupComplete` (already exposed, `useGeminiLive.ts:322`); start `connect()` at sheet-open (permission already granted case) instead of first mic tap; keep `/api/voice/live-token` warm. Then instrument the live path (timestamps: token, setup, speech-end→user-transcript, speech-end→first-agent-audio) — **setup slowness is tolerated; live-turn slowness is a bug** to be diagnosed from these numbers, not guessed.
9. **Chat UX contract (both modes): no streaming required.** All-at-once answers fine. Required: correct grounded answers; newest message at bottom with auto-scroll; in-session history persists across turns (voice transcript turns accumulate, both roles, rolling as they arrive). Rules out: SSE work on `/api/story/{id}/question`.
10. **Fix onboarding only after a fresh build reproduces it.** The reported misalignment is most likely the stale bundle; verify via Playwright on current `out/` first. Known genuine suspect: doubled top padding (safe-area + `pt-6`) at `InterviewChat.tsx:453`. Rules out: chasing ghosts in old-bundle screenshots.

### Module contracts

- **Reconcile/importance path (pipeline).** Given the day's candidates, collapse same-event dupes and emit `cluster_importance_by_story`; ranking must consume it whenever clustering is on. Must always: keep `block_by_category=False` merges from flipping a story into a category that contradicts its fetching interest (decision 3 applies post-merge); degrade to outlet-count fallback loudly if embeddings fail. Edge: embedding API outage mid-batch (partial reconcile → fall back whole-run, log).
- **Category assignment (pipeline).** Given a candidate with matched interest + themes: whitelisted theme → theme category may set depth-0; no whitelisted theme → fetching interest's root is authoritative, no `arts` default. Edge: story fetched by two interests in different roots (existing lowest-depth rule decides; log the conflict).
- **Section fill (assembly).** Leaf candidates below the importance floor don't fill leaf slots; the slot climbs/backfills with rung stamped. Edge: every leaf candidate below floor on a thin day → honest climb, never a padded leaf.
- **Poster gate (pipeline).** `DISABLE_POSTER_GEN=1` → zero image-model calls across all entry points; stories/audio/captions unaffected; source reels keep supplied images. Edge: flag unset (default) → current behavior, so re-enabling is env-only.
- **Build stamp (app).** Settings shows `<sha> · <build time>`; value baked at build, never fetched. Edge: dev server (no git) → shows `dev`.
- **Voice sheet (app).** Orb states: connecting (mic gesture disabled or buffered) → listening (only after `setupComplete`) → live. First utterance is never dropped silently — if audio arrives pre-setup, show "Connecting…" feedback. Latency marks logged per turn.
- **Chat sheets (app).** Turn list renders oldest→newest, autoscrolls to newest; voice input/output transcripts append as rolling partials; history survives within the sheet session.

### Milestones (coarse — slices from /to-issues)

- **M1 — Cost + trust floor.** True when: poster kill switch live (scheduled worker makes zero image calls, verified in logs); build stamp visible in Settings; a fresh `build:ios` on the founder's phone shows the current SHA, the chat onboarding, and the 5-tab library.
- **M2 — Feed correctness.** True when: branch clustering work committed + `ENABLE_SEMANTIC_CLUSTERING=1` in prod; category precedence + whitelist fix live; interest queries backfilled with loud-fail guard; importance floor live; and a live batch's per-section top reels spot-check against leading-outlet coverage with 0 mislabeled categories in the founder's 30.
- **M3 — Conversation surfaces.** True when: voice shows honest Connecting/Listening, pre-warms at sheet-open, first words never dropped; per-turn latency instrumentation reports numbers and any live-turn slowness has a diagnosed cause (fixed if in our code, quantified if upstream); typed + voice sheets meet the chat UX contract (bottom-anchored, persistent history, rolling transcripts).
- **M4 — Launch verification.** True when: Playwright walks every onboarding step on a fresh build with the top-gap resolved (fixed or proven not to reproduce); a `/go-live-check`-style E2E passes (onboarding → reel → voice → chat) on a build-stamped install; founder lives on the feed ≥ 2 days and signs off feed relevance.

### Riskiest assumption + de-risk

**That enabling semantic clustering actually makes sections lead with the day's top stories.** The importance math has never run live end-to-end. De-risked at M2 by the outlet-coverage spot-check on the first live batch — if top reels still miss what BBC/Reuters lead with for that topic, the fallback lever is score-weight tuning (β raise precedent exists: FSR-M3 0.3→0.45) with the same measurable acceptance, before any bigger rework. Secondary: embedding cost/run — measured on the first M2 batch; posters-off savings are the budget headroom.

## User Stories

1. As a follower of a sub-niche, I want its section led by the story leading outlets are covering today, so that my reel matches what I'd see on BBC/Reuters for that topic.
2. As a follower, I want the ladder honest — leaf first, climb only when the leaf is thin, rung visible — so filler never masquerades as my followed news.
3. As a user, I want every reel labeled with the right category, so that my Geopolitics slot never wears an Arts badge.
4. As the founder, I want any followed interest that can't ingest to scream in the batch summary, so empty sections are a visible bug, not a silent one.
5. As the founder, I want a build stamp in Settings, so I know in 2 seconds whether my phone runs current code.
6. As the founder, I want the archive path to guarantee a fresh bundle, so a stale TestFlight build is impossible by construction.
7. As a voice user, I want "Connecting…" until the session is truly live, so my first words are never spoken into a deaf session.
8. As a voice user, I want fast turns once live — and if they're slow, I want the pipeline instrumented so we know exactly where the time goes.
9. As a chat user (typed or voice), I want newest-at-bottom, persistent in-session history, and live rolling transcripts of both sides, so it feels like a conversation.
10. As the operator, I want `DISABLE_POSTER_GEN=1` to make image-model spend exactly zero (worker included), so testing burns no credits.
11. As a user, I want posterless reels to still look intentional (category wash, captions, audio), so the cost hold is invisible as a defect.
12. As a new user, I want every onboarding step rendering cleanly (Playwright-verified on a fresh build), so first contact isn't misaligned.
13. As the operator, I want all new pipeline logging structured JSON with `fix_suggestion`, so failures debug themselves.

## Implementation Decisions

- **Commit the branch first.** The uncommitted clustering/reconcile work on `claude/feed-source-revamp-plan-388edf` (embeddings.py, online_clusterer.py, reconcile.py + tests, daily_batch.py, pipeline_routes.py, run_live_batch.py) is the foundation of M2 — land it as its own slice with its existing tests.
- **Whitelist expansion is data-driven:** run one batch with counting on `theme_category_no_whitelisted_theme`, expand from the actual top-miss GDELT codes, not guesses.
- **Importance floor value** starts as a constant with a `# Reason:` note and is tuned against the M2 spot-check; do not build a config surface for it (Rule 2).
- **Voice instrumentation** = structured logs client-side (console + optional POST) with named marks; no analytics vendor.
- **No schema migrations expected.** If one becomes necessary, the migration-drift rule applies (pooler :6543 path, record in schema_migrations — see memory precedents).
- **Feed contract twin rule holds:** any `daily_feeds` metadata change lands in `src/types/feed.ts` + Python models together. Category precedence change must be checked against `SLUG_TO_CATEGORY` ↔ `feedBuckets.ts` sync (drift precedent 2026-06-17).
- **Design:** all UI touches follow `blip-design-guide.md`; no new visual language.

## Testing Decisions

Mock at boundaries (Gemini, BigQuery, Supabase, WSS). Per new/changed function: happy + failure + edge (CLAUDE.md minimum).

- **Category precedence:** table-driven — whitelisted theme wins; unmatched theme + interest-fetched → interest root; unmatched theme + no interest (beyond-bubble) → defined fallback that is never silently `arts`. A test that passes with the old arts-default is wrong (Rule 9).
- **Importance path:** with clustering on, ranking consumes `cluster_importance_by_story`; two same-section candidates where the fresher one has lower importance → the important one wins (this test encodes the whole point of M2).
- **Floor/ladder:** below-floor leaf candidates → climb with rung stamped; never a padded leaf.
- **Poster gate:** flag on → orchestrator returns None poster, zero client constructions across worker + batch entry points; flag off → unchanged.
- **Voice states:** connecting never shows LISTENING; transcript turns append rolling; history persists across turns.
- **Live E2E residuals (never skipped, Rule 12):** M2 = real batch + outlet spot-check; M4 = Playwright onboarding walk + fresh-install E2E on device.

## Out of Scope

- New features of any kind; poster quality/identity work (paused with generation).
- Typed-chat streaming (explicitly cut by founder).
- Edge-case hardening beyond core flows (founder: core must work; edge cases tolerated).
- Cross-session chat history persistence (in-session only).
- Velocity signal for importance (`cluster_velocity` often None — noted, not fixed here).
- Backlog issues #10/#11/#12/#21/#27 (valid, separate).

## Further Notes

- **Deviation from /cto step 0.4:** full `/improve-architecture` skipped in favor of the 4-agent targeted review (same precedent as prior PRDs); every touched module is covered above. Run `/improve-architecture` after M2 if a broad deepening pass is wanted.
- The founder asked for a systemic simplification review — `/grab-issue`'s per-slice simplify + slop-scan steps carry that; anything structural surfaces via `/improve-architecture`.
- Reference docs to touch during slices: `reference/ranking-spec.md` (importance floor + clustering-on), `reference/stack-notes.md` (DISABLE_POSTER_GEN, build-stamp recipe).
- 90-day/launch metric instrumentation from the old PRD (slots-without-fallback etc.) still stands; M2's spot-check is manual by design — cheap and directly answers the founder's bar.
