# PRD — Conversational Micro-Interest Onboarding + Niche-First Feed

**Date:** 2026-07-03
**Source:** documents/product-brief.md (brainstormed + approved 2026-07-03)
**Status:** Ready for /to-issues

> **Supersession notice (read first).** This PRD **supersedes the roots-only-onboarding decision** of the 2026-06-30 Feed-Source Revamp PRD (git `48a25b0`): deep interest extraction and niche keyword ingestion return as the core personalization mechanism. Everything else FSR shipped **stays and is load-bearing**: authority-weighted E1 importance, theme→category tagging, trusted-outlet top-stories backbone, source/cluster onboarding (M6a), source-led priority slots (M6b), long/short summary selector (M7). Owner approved the supersession explicitly.

## Problem Statement

A fixed taxonomy can never contain a person's actual micro-interests. "IPL auction drama" is not a node in any pre-built tree, so users pick the nearest broad category, the profile comes out shallow, and the feed reads like a generic newspaper instead of *their* feed. The FSR answer (personalization = follows only, news = shared backbone) under-serves users whose niches live in *news* — the cricket obsessive and the chip-industry nerd get the same 30 stories as everyone else who ticked "Sport" and "Tech".

## Solution

From the user's perspective:

- **Onboarding is a short chat.** The app asks; you tap bubbles. Each tap generates the next, more specific set of bubbles about what you just chose (Sport → Cricket → IPL → "auctions & transfers?"). Skip and "something else — type it" are always available. ≤ ~3 minutes, ~15 taps, out come 5–15 named micro-interests *in your own words*.
- **Your 30 is organized by your niches.** Sections are named in your vocabulary ("IPL — 4", "Silicon — 3"), not generic categories. A small "beyond your bubble" section (3–5 slots) keeps serendipity; followed YouTube/X sources keep their existing lead slots.
- **Dry days are honest.** No IPL story today → the slot fills from one level up the ladder, labeled: *"Nothing new in IPL today — here's cricket."* The ladder is the drill-down path you tapped during the interview.
- **Source suggestions stay** (existing M6a screen) and later regenerate from the micro-profile (fast-follow, out of MVP).

## Technical Foundation

### Tech stack (existing, fixed — one-line rationale each)

- **Frontend:** Next.js 15 static export + React 19 + Tailwind 4 + Capacitor 8 — the shipped app shell; the interview is one new onboarding stage inside `OnboardingFlow.tsx`'s existing state machine.
- **Backend/data:** Supabase (Postgres + RLS + email-OTP auth) — all interview outputs land in existing/extended tables.
- **Agent layer:** Python 3.12 worker (FastAPI on Railway) using `google-genai` — already serves runtime LLM calls (grounded Q&A), so interview bubble generation is a new worker endpoint, keeping the Gemini key server-side. Model: Gemini Flash-class for latency; structured JSON output.
- **Ingestion:** GDELT **BigQuery** adapter (`agents/ingestion/adapters/gdelt_bigquery.py`, written, unwired) replaces the throttled keyless DOC adapter for niche queries — one batched SQL for all interests, no 250-row cap. Adds `google-cloud-bigquery` + service-account credentials (BigQuery project `blip-498623` validated 2026-06-06).
- **Jobs:** Trigger.dev v4 daily batch — unchanged.
- **Hosting:** Vercel (SPA) + Railway (worker) — unchanged.
- **Languages:** TS (app) + Python (pipeline) — unchanged.

### Architecture

```
 Onboarding (Capacitor/Next SPA)                     Worker (FastAPI, Railway)
 ┌──────────────────────────────┐   POST /api/interview/turn   ┌──────────────────────────┐
 │ Interview chat stage          │ ───────────────────────────► │ Interview engine          │
 │ (bubbles, skip, free-text)    │ ◄─────────────────────────── │ Gemini Flash, JSON schema │
 └──────────────┬───────────────┘   next bubbles / final list   └──────────────────────────┘
                │ persist micro-interests (nodes + profile + ladder)
                ▼
 ┌──────────────────────────────┐        ┌───────────────────────────────────────────┐
 │ Supabase                      │        │ Daily pipeline (Python batch)             │
 │ interests (tree, minted nodes)│◄───────│ GDELT BigQuery: backbone top-stories      │
 │ user_interest_profile         │        │ (FSR, unchanged) + batched niche queries  │
 │ user_feed_allocation (+label) │        │ → tag → cluster → importance → assemble   │
 │ daily_feeds (+section,        │◄───────│ niche-first fill, ladder fallback + label │
 │   +fallback metadata)         │        └───────────────────────────────────────────┘
 └──────────────┬───────────────┘
                ▼
 Reel: section header in user's words; fallback slots labeled honestly
```

### Key design decisions

1. **Micro-interests are minted as real nodes in the existing `interests` tree** (children under the 8 roots, depth 1–3, `interest_search_query` generated at interview time), not a parallel table. Why: the entire tagging → ranking → assembly machinery already keys on `interests` + `user_interest_profile`; a parallel table would fork every downstream stage. Rules out: per-user private taxonomies (nodes are shared/global; two IPL fans converge on the same `sport.cricket.ipl` node via slug canonicalization, each keeping their own display label if their vocabulary differs).
2. **The interview is server-driven, one turn per request.** Client sends conversation state (taps + typed text so far); worker returns the next question + generated bubbles, or the terminal micro-interest list (label, canonical slug, parent chain = ladder, search terms). Why: prompt + key stay server-side; client stays a dumb renderer. Rules out: on-device generation, pre-built question banks.
3. **Bubble generation is grounded, not free.** Turn 1 bubbles = the 8 roots (fixed, free). Deeper turns are LLM-generated but constrained: ≤ 6 bubbles/turn, ≤ 3 drill-downs per lit-up root, always include "not really / skip" and "something else — type it". Terminal validation: every minted node must parse to a root-anchored slug and carry ≥ 2 concrete search anchor terms; otherwise it's re-asked or dropped, never silently invented. Why: bubble quality is a flagged soft spot; guardrails make bad generations recoverable.
4. **Ingestion = FSR backbone + batched niche queries, one BigQuery pass.** The trusted-outlet top-stories pull (M4) is untouched. Niche candidates come from the existing batched anchor-term SQL in the BigQuery adapter, now fed by all users' micro-interest `interest_search_query` rows. DOC adapter is retired from the daily path (kept for ad-hoc/coverage checks). Why: the DOC door (1 req/5 s, 250-row cap) cannot serve hundreds of niche queries. Rules out: per-interest DOC fan-out.
5. **Fallback happens at feed assembly, not ingestion, and is stamped on the row.** Assembly fills each niche section from direct-tagged stories; if short, it climbs the node's parent chain one level at a time. Each `daily_feeds` row gains section metadata: section label (user vocabulary), the interest node it was filled for, and — when climbed — the fallback source level for the honest UI label. Why: ingestion stays user-agnostic (shared pool, FSR principle); personalization stays a per-user assembly concern. Rules out: silent substitution (owner decision), per-user ingestion.
6. **`user_feed_allocation` grows a user-facing label + interest reference; the category enum stays for backbone/source slots.** Niche sections allocate by interest node with a text label; `youtube`/`x` and "beyond your bubble" slots keep enum semantics. Why: minimal migration, no enum explosion, sections render from one table. Rules out: renaming the enum per user.
7. **"Beyond your bubble" = 3–5 slots of backbone top-stories from roots the user did *not* light up on**, importance-ranked. Why: cheapest serendipity that reuses the backbone; no new ranking machinery.
8. **Existing profiles migrate by re-interview, not transformation.** Current root-level profiles keep working (a root is just a depth-0 section); the interview is offered to existing users as "rebuild my feed". Why: nothing to lossily transform — migration 0024 already collapsed old deep picks.

### Module contracts (plain prose — test intentions, not tests)

- **Interview engine (worker).** Responsibility: given conversation state, produce either the next question + bubbles or the terminal micro-interest list. Must always: include skip and free-text options; respect the 3-drill-down cap and ~15-tap budget; return root-anchored canonical slugs with ≥ 2 anchor terms per interest; never return an interest the user didn't tap or type toward. Edge cases: user skips everything (fall back to roots-only profile — the FSR baseline, feed still works); free-text gibberish or unmappable input (ask once more, then park it as root-level); duplicate niches across roots (dedupe by slug); LLM timeout/malformed JSON (client-visible retry, never a dead-end screen); the same niche typed by two users in different words (converge on one node, per-user labels).
- **Niche ingestion (pipeline).** Responsibility: one daily BigQuery pass returning backbone + niche candidates tagged to interest nodes. Must always: batch all active micro-interests into one query; cap per-interest candidates; stamp `StoryInterestTag` with node + depth; leave backbone top-stories logic untouched. Edge cases: interest with zero matches (empty is valid — fallback handles it); anchor terms matching junk (per-interest cap + existing dedup/importance downstream); BigQuery credential/billing failure (fail loud, fall back to DOC adapter for backbone only, log with fix_suggestion); brand-new node mid-cycle (picked up next batch, not silently missed forever).
- **Fallback-ladder assembly (pipeline).** Responsibility: fill each user's niche sections niche-first, climb the ladder when short, stamp section + fallback metadata. Must always: prefer direct-tagged stories; climb exactly one level at a time; label every climbed slot; respect followed-source priority slots and the beyond-bubble reserve; write ≤ 30 rows idempotently on `(feed_user_id, feed_date)`. Edge cases: whole ladder dry (leave slot for beyond-bubble backfill rather than empty feed); duplicate story matching two of the user's niches (one slot, dedupe across sections); `profile_is_strict` interests (never climb); user with only root-level profile (behaves exactly like today's FSR feed).
- **Interview chat UI (app).** Responsibility: render the chat, bubbles, skip/free-text, and hand the terminal list to persistence. Must always: work offline-tolerant (retry a failed turn); show progress; persist only at terminal confirm (no half-profiles); then hand off to the existing sources → build stages. Edge cases: mid-interview abandon (resume or restart cleanly; `user_onboarded_at` stays unset — the onboarding-gate rule from 2026-06-30 holds); back-navigation editing an earlier answer (downstream taps invalidated, re-generated).
- **Section rendering (app).** Responsibility: show section headers in the user's vocabulary and the honest fallback label on climbed slots. Must always: render from `daily_feeds` metadata only (no client-side inference). Edge cases: missing metadata on legacy rows (fall back to category label, never crash); long labels (truncate visually, never re-write the user's words).

### Milestones (coarse — slices come from /to-issues)

- **M1 — Interview engine + chat onboarding.** True when: a new user completes the chat interview on the phone in ≤ 3 min/~15 taps, and 5–15 micro-interest nodes + ladder + profile rows exist in Supabase; picker stage replaced; skip-everything path still onboards.
- **M2 — Niche ingestion + coverage census.** True when: BigQuery adapter is wired into the daily batch, niche candidates are tagged in the shared pool, and a per-niche coverage report exists over ≥ 3 days of real pulls for the 3 test personas' interests. **This is the de-risking milestone — it directly measures the riskiest assumption before any feed UI is built.**
- **M3 — Niche-sectioned feed with honest fallback.** True when: allocation schema migrated, assembly fills niche-first with labeled ladder climbs, Build-My-30 and the reel show user-vocabulary sections, beyond-bubble reserve works, followed-source slots unchanged.
- **M4 — Persona validation.** True when: founder + "cricket obsessive" + "chip-industry nerd" profiles run end-to-end (interview → batch → feed) and niche hit rate is measured against the ≥ 60% target; tuning pass done; go/no-go recorded.
- **(Fast-follow, out of MVP) M5 — Generated source suggestions** from the micro-profile (LLM-proposed, code-verified, curated safety net).

### Riskiest assumption + de-risk

**That micro-niche ingestion can fill the feed** — GDELT/BigQuery surfaces enough direct-niche stories that the fallback label is the exception. De-risked at **M2** by the coverage census on real personas *before* the feed UI exists (M3). If hit rate is structurally low, the cheap pivot is coarser niches (interview drills one level less) — the interview and ladder machinery survive unchanged.

## User Stories

1. As a new user, I want onboarding to feel like a short chat, so that expressing my interests isn't form-filling.
2. As a new user, I want to answer by tapping bubbles rather than typing, so that the interview is fast on a phone.
3. As a new user, I want each question's options to react to my previous answer and get more specific, so that I can reach interests no menu would list.
4. As a cricket-obsessed user, I want to land on "IPL — auctions and transfers" in ~3 taps, so that my profile captures my actual niche, not "Sport".
5. As a new user, I want a "not really / skip" bubble on every question, so that I'm never forced into an interest.
6. As a new user, I want a "something else — type it" option on every question, so that an interest the bubbles missed still gets captured.
7. As a new user, I want the interview to end in about 3 minutes / ~15 taps, so that personalizing doesn't feel like a chore.
8. As a new user, I want to see and confirm my extracted interest list (in my own words) before finishing, so that nothing wrong gets baked into my feed.
9. As a user who skips the whole interview, I want a working broad-category feed anyway, so that skipping isn't punished.
10. As a user, I want my typed free-text interest ("Formula 1 silly season") to become a real tracked niche, so that typing isn't a dead end.
11. As a user, I want my micro-interests remembered with the path I took to them, so that the app knows what "one level broader" means for me.
12. As a user, I want my 30 organized into sections named in my vocabulary ("Silicon — 3", "IPL — 4"), so that the feed visibly reflects what I said.
13. As a user, I want fresh stories from my niches filling those sections first, so that the interview's promise is kept daily.
14. As a user on a dry day for my niche, I want the slot filled from one level broader **with a label saying so**, so that I trust the app is really tracking my niche.
15. As a user, I want a small "beyond your bubble" section, so that I don't live in an echo chamber.
16. As a user who follows YouTube/X sources, I want those slots to keep leading my feed exactly as today, so that the revamp doesn't break my follows.
17. As a user with a strict interest, I want no fallback substitution on it, so that "only this" means only this.
18. As an existing (pre-revamp) user, I want a "rebuild my feed" entry point that runs the interview, so that I can upgrade to niche sections without losing my current setup.
19. As a user, I want to re-run or edit the interview later, so that my profile can evolve.
20. As the pipeline, I want all micro-interest queries batched into one BigQuery pass, so that niche ingestion scales past hundreds of niches without throttling.
21. As the pipeline, I want every niche candidate tagged to its interest node in the shared pool, so that assembly can fill sections without re-querying.
22. As the pipeline, I want the trusted-outlet backbone pull unchanged, so that the day's big stories still anchor every feed.
23. As the operator, I want a per-niche coverage report (direct hits/day per interest), so that the ≥ 60% hit-rate bet is measured before the UI ships.
24. As the operator, I want interview turns and ingestion logged as structured JSON with fix_suggestions, so that failures are debuggable.
25. As the operator, I want the interview's LLM cost/latency per completed onboarding tracked, so that the chat stays affordable and snappy.
26. As a user with a half-finished interview, I want resume-or-restart on next open (and no premature "onboarded" stamp), so that abandonment doesn't strand me on a broken feed.
27. As a user whose niche section is entirely dry up the whole ladder, I want the slots given to beyond-bubble stories, so that my feed is never short.
28. As two users who typed the same niche differently, we want to converge on the same underlying node (keeping our own words on screen), so that the shared pool stays deduplicated.

## Implementation Decisions

- **New worker endpoint** for interview turns (mirrors the Q&A endpoint's auth + graceful-failure contract: HTTP 200 with a typed error/retry body, never 5xx to the client).
- **Interests tree is the single interest store**: minted nodes are global, slug-canonicalized, root-anchored; per-user display labels live with the profile row, not the node. `interest_search_query` returns to being load-bearing (FSR M2 had demoted it) — for niche nodes only; backbone stays theme-keyed.
- **`user_interest_profile` re-admits deep rows** (depth ≥ 1) with the existing weight semantics; roots-only remains a valid degenerate profile.
- **Allocation schema**: `user_feed_allocation` gains a nullable interest-node reference + user-facing section label; enum categories remain for backbone/source/beyond-bubble slots. One migration.
- **`daily_feeds` rows** gain section label + filled-for-interest + fallback-level metadata so the client renders sections and honesty labels with zero inference.
- **Feed contract twin rule holds**: any shape change lands in `src/types/feed.ts` and the Python assembly models together.
- **BigQuery wiring**: add `google-cloud-bigquery` to requirements, service-account key via env (never committed), swap the adapter at the single pipeline seam, keep DOC adapter importable for coverage checks and backbone emergency fallback.
- **Onboarding gate rule (2026-06-30) is preserved**: `user_onboarded_at` stamps only at true flow end; interview abandonment never stamps.

## Testing Decisions

Test external behavior, not implementation (Rule 9). Mock at boundaries: Gemini client, BigQuery client, Supabase. Mirror existing prior art: `tests/lib/onboardingProfile.test.ts` (persistence), `tests/lib/onboarding/onboardingFlowSessionSkip.test.tsx` (flow state machine), Python pipeline sim harness (`agents.pipeline.sim`) for assembly.

- **Interview engine**: contract tests on turn output shape (bubbles ≤ 6, skip/free-text always present, terminal list validates slug + anchor terms); adversarial fixtures for malformed LLM JSON → retry body, never crash. Happy/failure/edge per function (CLAUDE.md minimum).
- **Assembly ladder**: table-driven cases — full niche pool (no climb), partial (one-level climb labeled), whole-ladder dry (beyond-bubble backfill), strict interest (no climb), duplicate story across two niches (one slot). These encode the *owner's honesty decision* — a test that passes with silent substitution is wrong.
- **Ingestion**: query-builder unit tests (all niches batched, per-interest caps) with a mocked BigQuery client; coverage-census output shape test.
- **UI**: component tests for section headers + fallback label rendering from row metadata, including legacy rows without metadata.
- **Live E2E residual** per milestone (real Gemini turn, real BigQuery pull, real persona feed) — named explicitly in each milestone's DoD, not silently skipped (Rule 12).

## Out of Scope

- Generated source suggestions from the micro-profile (M5 fast-follow) — existing M6a source/cluster screen bridges.
- Cookie- or OAuth-based YouTube/X subscription import — killed (App Store 5.2.2 + platform ToS; owner decision).
- X paid API, hand-maintained persona catalogs.
- Reel/audio/Q&A/voice/article layers, auth, iOS shell — unchanged.
- Engagement-signal profile updates (`profile_source='signal'`) — future.

## Further Notes

- **Deviation from /cto step 0.4**: full `/improve-architecture` was skipped in favor of a targeted architecture recon (this is a scoped revamp over a live codebase; the recon covered every touched module). If a broad deepening pass is wanted, run `/improve-architecture` separately.
- The brief's soft spots carry forward verbatim: 3–5-minute tolerance is founder conviction (watch completion rate from day one); 60% hit rate is a guess until M2; bubble quality unproven until M1.
- Old FSR PRD is recoverable at git `48a25b0`; the 2026-05-28 whole-product brief at `3a1da08`.
- Interview conversation transcripts: keep only the terminal extracted profile + tap path (the ladder); do not persist raw chat.
