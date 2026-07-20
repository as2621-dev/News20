# PRD — Feed Quality Reset & Source-System Removal

**Date:** 2026-07-18
**Source:** `plans/feed-quality-reset-brief.md` (approved 2026-07-18)
**Status:** Ready for /to-issues

> **Supersession notice.** This PRD supersedes the 2026-07-06 "Pre-Launch Hardening" PRD (recoverable at git `f1b7dfc`), whose M1/M2 work largely shipped on `claude/feed-source-revamp-plan-388edf`. It also supersedes `plans/m5-m6-personalization-sources-control-surface.md` — the YouTube/X/podcast/personality Sources surface is now being **removed, not extended**. Backlog issues #10/#11 (persona validation/census) remain valid; #12 (coarse Build-My-30), #21 (avatars), #27 (X handle seed) are **closed by this PRD's removal** — #21 and #27 are moot, #12's coarse-only decision is explicitly reversed by WS-E.

---

## Problem Statement

The daily 30-reel briefing is not trustworthy. Audited row-by-row against prod data for the founder's 2026-07-07 briefing:

- Only **~5 of 30** reels genuinely matched their displayed category tag.
- **11 of 30** were junk filler — a venison donation, CBSE school-board news, an obscure REIT press release, "federal funding in Hawaii", and one reel headlined literally **"Language Magazine"** (a source masthead).
- Tags contradicted content: a geopolitics story tagged **sport**, Bitcoin tagged **geopolitics**, OPEC tagged **tech**, DeepMind tagged **wildcard**.
- The Build-My-30 contract ("my first 5 slots are AI") appeared ignored.
- The 4 YouTube + X slots produced **zero** reels, and their budgets silently spilled elsewhere — geopolitics overfilled to 8, sport to 9, business filled only 2 of 6.

This is a product-trust failure of a specific kind: the user configured exactly what they wanted, and the product visibly did not honor it. Every root cause below was confirmed in code **and** re-verified against the working tree on 2026-07-18.

### Root causes (verified)

| # | Root cause | Evidence | Effect |
|---|---|---|---|
| RC1 | **Stale segment-validation set.** `_VALID_SEGMENT_SLUGS` is still the pre-taxonomy 5-set `{geopolitics, markets, tech, sport, wildcard}` with `_DEFAULT_SEGMENT_SLUG = "wildcard"`; never widened when the 8-root taxonomy landed. | `agents/pipeline/persist_helpers.py:44-47`; default applied at `:578` and `:586` | Every ai/business/environment/politics/arts story persists mislabeled. Prod 07-07: **0 of 67** stories tagged ai/business/arts. `src/types/feed.ts:41` `SegmentKey` is *already* the correct 8-root set — a live Py/TS contract break. |
| RC2 | **No notability gate in the live path.** Admission = ANY single anchor term regex-matching the GDELT haystack. A top-weight interest alone scores `1.0 × 1.0 × α 0.5 = 0.5`, clearing the `0.20` threshold on affinity alone; the produce floor `0.05` sits below a single-outlet story's importance. The trusted authority-outlet filter is **built, tested, and has zero production callers**. | `agents/ingestion/adapters/gdelt_bigquery.py:207-257`; `agents/pipeline/stages/ranking.py:62,84,141-143,301-305`; `agents/pipeline/produce_gate.py:66-67`; `ingest_trusted_outlets` at `agents/ingestion/interest_keyed_pipeline.py:512` — callers are tests only | One-outlet local notices and PR-wire items reach the top-30. |
| RC3 | **Keyword-match false positives.** Single generic terms both admit and tag: "Foundation"→AI interests (venison, an SCP film), "Federal"→interest-rates-fed (Hawaii funding), "India"→cricket.india (an Indonesia port story — also the origin of its **sport** tag), "data center"→data-center-buildout (a zoning lawsuit). | `_BATCH_SQL` term match + SP1 `story_interests` tagging | Junk fills the slots the user cares most about — the founder's 5 AI slots were nearly all false positives. |
| RC4 | **Headline provenance broken.** GDELT `<PAGE_TITLE>` can be a site masthead; the cluster representative is the **earliest-published** member (worst title wins); editorial rewrite is fail-open; no sanity check for `title == outlet name` or too-short. | `gdelt_bigquery.py:214`; `agents/ingestion/dedup.py:186-190,326`; `agents/pipeline/stages/editorial.py:124-132`; `agents/pipeline/persist.py:411` | The "Language Magazine" reel. |
| RC5 | **The honest niche assembler never runs.** `assemble_niche_feed` self-delegates to the coarse `assemble_user_feed` when no allocation row carries `allocation_interest_id`; Build-My-30 saves coarse-only rows, and `build_niche_allocation` is called only by tests and `scripts/allocate_niche_sections.py`. All migration-0026/0027 section metadata is NULL in prod. | guard at `agents/pipeline/feed_assembly.py:1121`, delegation at `:1130`; `agents/pipeline/niche_allocation.py:314` | No honest section labels or climb levels. **Note:** the coarse path *does* honor Build-My-30 order — the founder's "first 5 AI" slots genuinely were AI slots, mislabeled by RC1 and junk-filled by RC3. Conversely the niche path currently **ignores** drag order (sorts by profile weight) — so turning it on naively would break G5 in a new way. |
| RC6 | **Chip/headline layout bug.** `.seg-chip` is `inline-flex` and the headline `<button>` defaults to inline-block → they render on ONE line ("Wildcard Language Magazine"). | `src/styles/blip-flow.css:2018-2019`; `src/components/blip/reel/ReelStage.tsx:311-319` | Broken-looking reel header. |
| RC7 | **Source slots are dead weight.** X ingestion is dead (xAI Live Search 410s); YouTube produced zero reels; unfilled source budgets soft-roll into other categories, silently distorting the allocation. | `agents/pipeline/feed_assembly.py:601,730` | 4 of 30 slots wasted; the allocation contract is broken. |

**Newly discovered (2026-07-18 inventory), and it de-risks WS-D:** the source system is **already substantially dormant**. `RUN_SOURCES` is referenced only in an `.env.example` comment — *no code reads it*. `trigger/sourceIngestion.ts` POSTs every 2 hours to `/ingestion/sources`, which **is not a registered worker route** (the worker serves `/healthz`, `/pipeline/daily`, `/feed/assemble-for-user`, `/api/interview/turn`, `/api/sources/search`, `/api/story/{id}/question`, `/api/story/{id}/corpus`, `/api/voice/live-token`). So the only live source surfaces are `/api/sources/search` and the x-theme path gated behind `RUN_X_THEMES=0`. Removal is far closer to dead-code deletion than to a behavior change — but this must be **confirmed, not assumed** (Rule 12).

## Solution

- Every reel wears **the right category tag** — the label matches what the story is actually about.
- Nothing reaches my 30 unless it is **plausibly news**: corroborated by multiple outlets, or carried by an authority outlet. No single-outlet PR-wire or local-notice filler.
- A story only fills my "AI" slot when it is **genuinely about AI** — not because it contained the word "Foundation".
- Headlines are **informative sentences**, never a masthead or a two-word fragment.
- **Build-My-30 is visibly honored** — the counts I set and the order I dragged. When a niche genuinely has nothing new today, the reel says so honestly ("Beyond your bubble") instead of quietly padding.
- **Sources are gone.** blip is news reels. The 30 slots are all news.
- In exchange, the onboarding conversation **goes deeper on what I actually care about** — sub-niches, the specific people/teams/companies I follow, and an explicit "never show me this" — so the personalization sources were supposed to provide comes from the interest layer instead.

## Technical Foundation

### Tech stack

Existing, unchanged (Rule 2 — this PRD removes components, it adds none):

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Next.js 15 static export + Capacitor 8, deployed on Vercel | Shipped; offline-first bundle is the iOS delivery path. |
| Backend / data | Supabase (Postgres + auth + RLS) | Shipped; migrations are the schema seam. |
| Pipeline / agents | Python 3.12 FastAPI worker on Railway | Shipped; the batch is a heavy multi-pass best unit-tested in Python. |
| Ingestion | GDELT DOC + BigQuery | Explicit non-goal to replace (brief §5). |
| Models | Gemini via `google-genai` — Flash-class for interview/editorial, `gemini-embedding-001` @768d for clustering **and now relevance** | Already wired for clustering; reusing it for the WS-C relevance check adds no new dependency. |
| Jobs | Trigger.dev v4 | Shipped; loses one task (`sourceIngestion`). |
| Languages | TypeScript (app) + Python (pipeline) | Shipped; the Py/TS twin discipline is load-bearing here. |

### Architecture — what changes

```
                    ┌─────────────────────────────────────────┐
GDELT BigQuery ────► │ INGESTION                               │
                    │  • [WS-B] authority-domain filter WIRED  │
                    │    (ingest_trusted_outlets → live path)  │
                    │  • [WS-C] phrase anchors; generic single │
                    │    terms can no longer admit             │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │ TAGGING & PERSIST                        │
                    │  • [WS-A] segment = 8-root, derived from │
                    │    best interest root; NO wildcard default│
                    │  • [WS-A] headline sanity gate; cluster  │
                    │    rep = best title, not earliest        │
                    │  • [WS-C] embedding relevance check      │
                    │    story ↔ matched interest              │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │ RANK & PRODUCE GATE                      │
                    │  • [WS-B] notability hard cut            │
                    │    (≥2 outlets OR authority-tier)        │
                    │  • [WS-B] affinity alone cannot clear    │
                    │    threshold; within-category importance │
                    │    normalization fixed                   │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │ FEED ASSEMBLY (daily_feeds)              │
                    │  • [WS-E] niche allocator WIRED, honoring│
                    │    Build-My-30 drag order                │
                    │  • honest ladder: leaf → climb → strict  │
                    │    → "Beyond your bubble", rung stamped  │
                    │  • [WS-D] 30 topic slots, 0 source slots │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │ APP                                      │
                    │  • [WS-A] chip/headline layout fix       │
                    │  • section labels surfaced from metadata │
                    │  • [WS-D] Sources tab GONE (5→4 tabs)    │
                    │  • [WS-E] 3-phase deeper interview       │
                    └─────────────────────────────────────────┘

REMOVED ENTIRELY (WS-D): YouTube adapter · X adapters + xai_client + x_resolver ·
tweet_screenshot · x_theme_* · cluster_sweep · source_pipeline · scheduler cadences ·
produce_source_reels · agents/catalog/* · src/components/sources/* · SourcesScreen ·
onboarding source pickers · trigger/sourceIngestion.ts · /api/sources/search ·
content_sources + 8 sibling tables · x-theme tables + daily_feeds x-theme columns
```

### Key design decisions

1. **Segment slug is derived, never defaulted to a junk bucket.** `_VALID_SEGMENT_SLUGS` becomes the 8-root taxonomy from `agents/pipeline/categories.py::TOPIC_CATEGORIES`; the `"wildcard"` default is deleted. When no tag resolves, the segment comes from the story's **best-matching interest's root**; if even that is absent, the story is **rejected with a loud structured log**, not filed under a junk label. *Rules out:* any silent fallback bucket — the exact bug class that produced RC1.

2. **The Py/TS twin is enforced by a test, not by discipline.** RC1 existed because `SegmentKey` (TS) and `_VALID_SEGMENT_SLUGS` (Py) drifted silently for weeks, and the same drift class hit `SLUG_TO_CATEGORY` in 2026-06-17. A test asserts the Python 8-root set equals the TS `SegmentKey` union, parsed from source. **There are three allocation twins, not two** — `src/lib/feedBuckets.ts:164` (`DEFAULT_ALLOCATION_SEGMENTS`), `agents/pipeline/categories.py:138` (`DEFAULT_FEED_ALLOCATION`), and `niche_allocation.py` which *consumes* them. `categories.py` is the one the brief missed; miss it and the twins desync at 26 vs 30. *Rules out:* recurrence by convention alone.

3. **No historical backfill of mis-slugged stories** (founder call, 2026-07-18). The code fix applies to new stories only. **Accepted debt, stated honestly:** the Archive tab will show contradictory tags on stories older than the fix. If that proves annoying in use, a bounded backfill is a later slice — not a blocker for launch trust, which is about *today's* briefing.

4. **Notability = `≥2 distinct outlets` OR `authority-tier ≥ threshold`, with a thin-niche relaxation** (founder call). A single-outlet story from an authority outlet passes; a single-outlet PR-wire item does not. **The relaxation is what keeps G2 from eating G5:** when a leaf niche has fewer than a floor number of candidates surviving the gate, it drops to a relaxed rule rather than starving — and when it does, the slot is **stamped as relaxed** so the honesty ladder can surface it. Wired by giving the live path the authority domains that `ingest_trusted_outlets` already knows how to use. *Rules out:* a single global hard cut that would collapse thin niches like AI-interpretability into permanent "Beyond your bubble".

5. **Relevance is a two-key lock: lexical AND semantic.** A story may fill an interest's slot only if it passes phrase-level anchor matching *and* an embedding-similarity check between the story and the matched interest, using the already-wired `gemini-embedding-001`. Per-interest term hygiene bans standalone generic anchors ("foundation", "federal", "india", "trust"); short/ambiguous terms additionally require a **title** match, not just a haystack match. *Rules out:* the whole RC3 false-positive class, which single-signal matching cannot close — lexical alone admitted venison, and semantic alone is too loose to be a gate.

6. **Headline quality is fail-closed.** At persist: reject a title that equals its outlet name or falls below a minimum word count; choose the cluster representative by **best title/outlet quality**, not earliest-published; if editorial rewrite fails on a garbage title, retry or substitute another cluster member's title — never pass the garbage through. *Rules out:* fail-open rewrite, which is how a masthead reached the reel.

7. **Removal is staged so that each stage is safe by construction, and stage 1 starts by proving dormancy.** Order: (1) confirm-then-stop ingestion — verify `RUN_SOURCES` and `/ingestion/sources` really are inert, then delete the Trigger.dev cron **before** removing any route; (2) UI + allocation prune as **one atomic change** across all three twins, rebalanced to 30 topic slots; (3) pipeline module deletion; (4) DB last, via **new forward migrations only**; (5) config/env cleanup. *Rules out:* removing a route while a cron still calls it, and — critically — reserving phantom slots by pruning UI and allocation separately.

8. **Deletion is by explicit inventory, never by path glob.** Three name collisions are live hazards: `agents/pipeline/clustering/*` is **story** clustering, `agents/pipeline/importance/source_tiers.py` is **outlet-authority** tiering, and `scripts/seed_catalog/backfill_*.py` is **interest-taxonomy** work. All three match a "source"-ish grep and none may be deleted. A glob-driven removal would take out the story-clustering engine. ~92 files delete; ~27 need surgical edits (heaviest: `feed_assembly.py`, `daily_batch.py:1058-1176`, `src/lib/onboardingTerminal.ts` at 51 hits).

9. **The 30 rebalances to `ai 5 · tech 5 · geopolitics 5 · business 5 · politics 2 · environment 2 · sport 3 · arts 3`.** The freed 4 source slots go to the four highest-weighted roots, preserving the existing shape rather than inventing a new distribution. Enum values `'youtube'`, `'x'`, `'podcasts'` **stay in the `feed_category` Postgres enum** — dropping enum values requires a type swap that isn't worth the risk; writes are blocked at the app layer instead.

10. **The niche allocator ships only after it respects Build-My-30 drag order.** Today `build_niche_allocation` sorts by profile weight, which would silently override the user's explicit ordering — turning it on first would fix RC5 while breaking G5 in a fresh way. Drag-order fidelity is a **hard prerequisite**, tested before wiring. *Rules out:* flipping users to niche rows as a config change; there is no env flag — assembly path is chosen purely by allocation-row shape.

11. **The interview absorbs the removed Sources budget rather than just dropping it.** Phases go 5 → 3: `INTERESTS → TUNE → YOUR 30`; the YOUTUBE and X CLUSTERS phases are deleted, `source_follows` and `top_split` leave the terminal schema, and the budget card's news total goes 20 → 30. The freed conversational turns fund deeper elicitation: sub-niche drill-downs, WHO-drill answers **persisted as entity follows** (today they inform anchor terms but aren't durable follows), and explicit "never show me" capture into `user_mute_terms`. *Rules out:* a shorter onboarding — the interview stays the same length and gets deeper, because it is now the *only* personalization control surface.

12. **Validation requires a fresh live batch, not a re-assembly.** `daily_feeds` fills only from stories produced in the **same run** (one-pass constraint), and stale rows idempotent-skip the target user. Every acceptance check below is run against a newly generated briefing.

### Module contracts

**Segment resolution** (`persist_helpers`) — *Responsibility:* assign exactly one 8-root segment to every persisted story. *Must always:* emit a member of the canonical 8-root set; derive from the story's best-matching interest root when tags don't resolve; log structured with `fix_suggestion` on every fallback. *Edge cases:* no tags at all; tags resolving to two different roots (lowest match-depth wins, conflict logged); a legacy slug (`markets`, `wildcard`) arriving from old data — mapped or rejected, never propagated; empty interest lookup — must reject, not default.

**Notability gate** (ingestion + produce gate) — *Responsibility:* decide whether a candidate is plausibly news. *Must always:* pass ≥2 distinct outlets or an authority-tier outlet; count outlets **distinctly** (syndication of one wire item is not corroboration); stamp the relaxation rung when a thin niche invokes it. *Edge cases:* thin niche where every candidate fails (→ relaxed rule, stamped, never a silent pad); a syndication burst of one PR item across 12 content farms (→ must NOT count as 12 outlets); authority-domain list unavailable (→ fail loud, don't silently disable the gate).

**Interest matching** (anchor + relevance) — *Responsibility:* decide whether a story genuinely concerns a given interest. *Must always:* require phrase-level anchor match; require a title match for short/ambiguous terms; require embedding similarity above threshold before the story may fill that interest's slot; never let a banned standalone generic term admit alone. *Edge cases:* the four known false positives (venison/"Foundation", Hawaii/"Federal", Indonesia-port/"India", zoning/"data center") must each be rejected — these are the regression tests; embedding API failure mid-batch (→ fall back to strict lexical, log loudly, never fail open); a legitimately short interest name that *is* the story's subject.

**Headline provenance** (dedup + editorial + persist) — *Responsibility:* every published reel carries an informative sentence. *Must always:* reject `title == outlet name` and sub-minimum word counts; pick the cluster representative by title quality; fail closed when rewrite fails. *Edge cases:* every member of a cluster has a bad title (→ drop the story rather than publish a masthead); a legitimately short but informative headline near the word floor; rewrite returning malformed JSON.

**Feed assembly** (`feed_assembly` + `niche_allocation`) — *Responsibility:* fill 30 slots honoring the user's counts **and order**, labeling each slot honestly. *Must always:* preserve Build-My-30 drag order in the niche path; stamp the ladder rung (leaf / climbed / strict / beyond-bubble / relaxed-notability); sum to 30 with zero source slots; apply mute terms as hard filters at assembly. *Edge cases:* a niche with zero candidates (→ honest climb, never a padded leaf); removal shifting every downstream `sort_order` because source rows currently sort first; a user with legacy source follows in the DB (→ must assemble 30/30 without error); a user whose allocation still contains youtube/x rows post-migration.

**Interview engine** (`agents/interview` + onboarding) — *Responsibility:* elicit a durable interest profile in one conversation. *Must always:* produce root-anchored slugs with ≥2 anchor terms, every item traceable to a tap or typed text; persist WHO-drill answers as entity follows and SKIP answers as mute terms; sum the budget card to 30; deterministic fallback wording when the LLM fails (HTTP 200 always, never a dead-end screen). *Edge cases:* user skips everything (→ roots-only profile, feed still works); gibberish free text (→ one clarifying turn, then park at root level); re-run via rebuild-my-feed (→ clean-replace, no orphaned mutes/follows/allocation rows).

### Milestones

- **M1 — Labels and headlines are honest (WS-A).** True when: a freshly generated founder briefing has ≥28/30 tags matching story subject, zero masthead or fragment headlines, and the chip/headline render on separate lines; the Py/TS twin test is green; prod stories from that batch show non-zero ai/business/environment/politics/arts counts and zero new `wildcard`/`markets` segments.
- **M2 — Only real news gets in (WS-B + WS-C).** True when: the authority-domain filter runs in the live path; a fresh batch's founder briefing contains **zero single-outlet junk stories**; each of the four named false positives is rejected by test and absent from the live run; thin niches invoke the relaxation rung rather than starving.
- **M3 — Sources are gone (WS-D).** True when: dormancy of `RUN_SOURCES` and `/ingestion/sources` is confirmed in writing; grep-clean of youtube/x/podcast/personality across `src/` and `agents/` outside migration history; no source cron in Trigger.dev; no `/api/sources/search` route; allocation sums to 30 with zero source slots across **all three** twins; existing users — including those with legacy source follows — still assemble 30/30 with no errors.
- **M4 — Deeper personalization replaces what was removed (WS-E).** True when: the niche allocator is wired into Build-My-30 save + batch and **provably preserves drag order**; the honest ladder and section labels render in prod for real users; the 3-phase interview ships with sub-niche drills, entity follows persisted from WHO drills, and "never show me" capture; a founder re-onboard produces a profile strictly richer than today's.

### Riskiest assumption + how we de-risk it

**That tightening admission (M2) doesn't starve the feed.** Every fix in WS-B and WS-C *removes* candidates; the founder's 07-07 pool produced 67 stories of which most were junk, so it is entirely possible that a correct gate leaves fewer than 30 placeable stories and the briefing collapses into "Beyond your bubble" — trading a junk feed for an empty one. **De-risked at M2**, before removal or interview work: the first gated batch reports candidates-surviving-per-niche alongside the quality audit. If niches starve, the lever is the thin-niche relaxation floor (decision 4) — already designed in, tuned against the same acceptance audit — not a rollback of the gate. Secondary risk: the embedding relevance check adds per-story cost on top of clustering; measured on the first M2 batch, with posters still off as budget headroom.

## User Stories

1. As a reader, I want every reel's category tag to match what the story is actually about, so my Geopolitics slot never wears a Sport badge.
2. As a reader, I want stories about AI in my AI slots, so a venison donation never occupies the slot I care most about.
3. As a reader, I want nothing in my 30 that only one outlet bothered to publish, so PR-wire items and local notices stay out.
4. As a reader following a genuinely thin niche, I want that niche to still get filled honestly, so tightening quality doesn't empty my feed.
5. As a reader, I want every headline to be an informative sentence, so I never see a reel titled "Language Magazine".
6. As a reader, I want the category chip and headline on separate lines, so the reel header doesn't read as one garbled sentence.
7. As a user who dragged my Build-My-30 order, I want my first slots to be the category I put first, so the control I was given is real.
8. As a user, I want the slot counts I set to be the counts I get, so nothing silently overfills.
9. As a user whose niche has nothing new today, I want the reel to tell me so ("Beyond your bubble"), so filler never masquerades as my followed news.
10. As a user, I want my mute terms to hard-filter my feed, so "never show me this" means never.
11. As the founder, I want stories that can't be honestly categorized to be rejected loudly, so a junk bucket never absorbs a bug again.
12. As the founder, I want the Python and TypeScript taxonomies asserted equal by a test, so the drift that caused this never recurs silently.
13. As a user, I want the Sources tab and all YouTube/X/podcast/personality UI gone, so the app stops offering something it doesn't do.
14. As a user, I want all 30 slots to be news, so no slot is reserved for content that never arrives.
15. As an existing user with legacy source follows, I want my feed to keep assembling 30/30 without errors after removal.
16. As a new user, I want onboarding to never mention sources, so the product's promise matches what it delivers.
17. As a new user, I want the interview to drill into my sub-niches, so "sport" becomes "the specific series and players I follow".
18. As a new user, I want the people/teams/companies I name to become durable follows, so telling it once is enough.
19. As a new user, I want to say explicitly what I never want to see, so my feed is defined by exclusion as well as inclusion.
20. As a returning user, I want rebuild-my-feed to cleanly replace my profile, so re-onboarding leaves no orphaned follows, mutes, or allocation rows.
21. As the operator, I want confirmation in writing that the source cron and ingestion path were already inert, so removal risk is measured rather than assumed.
22. As the operator, I want every new gate and fallback to emit structured JSON with `fix_suggestion`, so failures debug themselves.
23. As the operator, I want each batch to report candidates-surviving-per-niche, so I can see starvation before a user does.
24. As the operator, I want DB objects dropped only by new forward migrations after code stops reading them, so removal is never a live schema race.

## Implementation Decisions

- **Sequencing is quality-then-removal** (founder call): WS-A → WS-B/WS-C → WS-D → WS-E. Prove the quality jump on a regenerated founder feed *before* executing removal, so a regression during removal is attributable.
- **Three allocation twins change together or not at all:** `src/lib/feedBuckets.ts`, `agents/pipeline/categories.py`, and the `niche_allocation.py` consumer. Same commit, same slice.
- **New forward migrations only.** Never edit an applied migration. Apply via the IPv4 session pooler on `:6543` (`:5432` times out — 2026-06-17 precedent), and record in `schema_migrations`. Drop order: x-theme tables and `daily_feeds` x-theme columns first, then `content_sources` and its 8 siblings, then taxonomy roots. `0032` already ships a commented rollback — reuse it. `feed_slot_kind` is plain text, not an enum: no migration needed, just stop writing `'source'`.
- **`agents/catalog/*` goes with the sources** — it exists solely for source-cluster resolution.
- **Verify before deleting `archetypes`** (`0009:218`) and the shared `SourceAdapter` ABC in `adapters/base.py`, which GDELT also implements.
- **Thresholds are constants with `# Reason:` notes**, tuned against the M2 audit. No config surface (Rule 2).
- **Reference docs to sync during slices:** `reference/interview-onboarding-spec.md` (5→3 phases, terminal schema, persistence mapping — the largest rewrite), `reference/ranking-spec.md` (notability gate + relevance check), `reference/supabase-schema.md` (dropped objects), `reference/api-contracts.md` (`/api/sources/search` gone, feed types narrowed), `reference/conventions.md` (twin-test rule). **Delete after removal:** `reference/source-catalog-taxonomy.md`, `reference/source-reels-spec.md`, `reference/sources-reuse-map.md`. README sync at the end of WS-D.
- **Design:** all UI touches follow `blip-design-guide.md` (repo root, source of truth). No new visual language — the chip fix and section labels use existing tokens. No remote design-system pick needed.
- Biome and Ruff must pass on every slice.

## Testing Decisions

Mock at boundaries (Gemini, BigQuery, Supabase). Per new/changed function: happy + failure + edge (CLAUDE.md minimum). Tests must encode *why* (Rule 9) — several below are written specifically so they fail if the business rule is weakened.

- **Segment resolution:** table-driven over all 8 roots plus the unresolvable case. A test that passes while a `"wildcard"` default exists is wrong — assert the fallback **rejects and logs**, not that it returns something.
- **Py/TS twin:** parse `SegmentKey` from `src/types/feed.ts` and assert set-equality with the Python 8-root set, and allocation-sum equality across all three twins. This test's whole purpose is to fail on drift.
- **Notability:** ≥2 distinct outlets passes; 1 non-authority outlet fails; 1 authority outlet passes; **12 syndicated copies of one wire item fail** (this is the test that encodes "corroboration ≠ syndication"); thin niche invokes relaxation and stamps the rung.
- **Relevance — the four named false positives are the regression suite:** venison/"Foundation", Hawaii/"Federal", Indonesia-port/"India", zoning/"data center". Each must be rejected. Plus the inverse: a genuine story matching the same interest must still pass, so the fix isn't just "reject everything".
- **Headline gate:** `title == outlet name` rejected; sub-floor word count rejected; cluster rep chosen by quality not recency; all-members-bad → story dropped, never published.
- **Assembly:** Build-My-30 drag order preserved through the niche path (the test that guards decision 10); counts honored; rung stamped on every non-leaf fill; sums to 30 with zero source slots; a user with legacy source follows assembles 30/30.
- **Removal:** a grep-based test asserting no youtube/x/podcast/personality references in `src/` or `agents/` outside migration history — cheap, and it's the only way G6 stays true over time.
- **Interview:** terminal schema has no `source_follows`/`top_split`; budget sums to 30; WHO-drill answers land as entity follows; SKIP answers land as mute terms; skip-everything yields a working roots-only profile; re-run clean-replaces with zero orphans.
- **Live residuals (never skipped, Rule 12):** M1/M2 acceptance is a **fresh live batch** audited row-by-row — the one-pass constraint means re-assembly of stale rows proves nothing. M3 requires a live 30/30 for an existing user post-removal. These are manual by design and must be reported with actual numbers, not asserted.

## Out of Scope

- New content modalities (video, podcasts-as-audio).
- Any rewrite of ingestion — GDELT DOC + BigQuery stays the backbone.
- Reel UI redesign beyond the chip/headline fix and section-label surfacing.
- Backfilling mis-slugged historical stories (founder call — see decision 3).
- Dropping `'youtube'`/`'x'`/`'podcasts'` values from the `feed_category` Postgres enum.
- Poster quality/identity work (generation remains cost-held).
- In-app resurfacing UI for deferred interview questions (the record ships; the UI is a fast-follow).
- Backlog #10/#11 (persona validation/census) — valid, separate.

## Further Notes

- **Deviation from `/cto` step 0.4:** a full `/improve-architecture` was skipped in favor of the brief's 5-agent code investigation (dated today, file:line evidence) plus two verification sweeps run during this session — the same precedent the 2026-07-06 PRD set. Every module touched here is covered by a contract above. Run `/improve-architecture` after M3, when ~92 files have been deleted and the module graph has genuinely changed shape.
- **Corrections folded in from verification** (the brief was right in substance, imprecise in three places): ranking lives at `agents/pipeline/stages/ranking.py`, not `agents/pipeline/ranking.py`; there is no "affinity default 0.5" — `AFFINITY_WEIGHT = 0.5` is the α weight and `profile_weight` defaults to 1.0, so the arithmetic (0.5 > 0.20) holds but the fix targets the weighting, not a default; the TS symbol is `DEFAULT_ALLOCATION_SEGMENTS`, and its Python twin is in `categories.py`, not `niche_allocation.py`.
- **Evidence pack:** the 07-07 30-row audit, allocation table, and per-category production counts are in the 2026-07-18 investigation (memory: `news20-0707-feed-rca-2026-07-18`).
- **Deployment discipline:** every change reaches the founder's iPhone before a verification ask — build → `cap sync` → `xcodebuild` → `devicectl install+launch`. No "hit Run in Xcode" hand-offs.
