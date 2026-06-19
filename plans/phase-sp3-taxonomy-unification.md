# Phase SP3: Unify the taxonomy on the picker tree (8 roots) + finish breaking removal

**Milestone:** M1 — Kill breaking + taxonomy cleanup (`plans/shared-pool-rework-master-plan.md`)
**Status:** Not started
**Estimated effort:** L

## Goal
Onboarding, "Build your 30", and the reel chip all draw the **same canonical category set = the 8 onboarding picker roots** (`ai, geopolitics, business, environment, politics, tech, sport, arts`) + the 2 source axes (`youtube, x`) — same labels and same accent colors on every surface, with **no folding** (ai→tech, geopolitics/politics/environment→world, arts→culture are retired). No `breaking` *allocation* concept remains in any script.

## Context for the executor
**Owner decision (2026-06-18):** the picker tree (`src/lib/pickerSeedTree.ts`, 8 depth-0 roots) is the single source of truth; this **supersedes** the master plan's earlier "5 topic categories" fold. Today three vocabularies exist (see `~/.claude/plans/a-few-things-to-bright-lamport.md` for the full map): picker roots (8) → folded design buckets (`world_politics, tech_science, markets, sport, culture` + youtube/x) → reel `segment_slug` (5 legacy: `geopolitics, markets, tech, sport, wildcard`). The fold maps are `agents/pipeline/categories.py` `SLUG_TO_CATEGORY` and `src/lib/feedBuckets.ts` `PICKER_ROOT_TO_CATEGORY_BUCKET`. Subcategories (dotted slugs like `sport.cricket`) stay **underneath** the roots for demand-sizing/classification — only the *root* is the visible category. **KEEP** the `story_is_breaking` velocity signal (Detail templates); only the breaking *allocation/slot* concept dies. The default 30-slot allocation must re-sum to 30 across the new 8+2 categories — exact split is an **open question** for the owner (see below).

## Sub-phases

### Sub-phase 1: Canonical taxonomy in the Python pipeline
- **Files touched:** `agents/pipeline/categories.py` (`FeedCategory`, `TOPIC_CATEGORIES`, `SOURCE_CATEGORIES`, `SLUG_TO_CATEGORY`, `DEFAULT_CATEGORY`, `DEFAULT_FEED_ALLOCATION`, `CATEGORY_FLOOR`, `empty_category_buckets`, `category_for_slug`).
- **What ships:** `FeedCategory` = the 8 picker roots + `youtube`/`x`; `SLUG_TO_CATEGORY` maps each root to itself and every known subcategory root to its picker root (no cross-fold); `TOPIC_CATEGORIES` = the 8 roots; `DEFAULT_FEED_ALLOCATION` + `CATEGORY_FLOOR` re-summed to 30 over the new set; `empty_category_buckets` returns all 10 keys.
- **Definition of done:** `pytest tests/agents/pipeline/test_categories.py` asserts `category_for_slug("sport.cricket.india")=="sport"`, `category_for_slug("ai.interpretability")=="ai"` (NOT `tech_science`), `category_for_slug("politics.x")=="politics"` (NOT `world_politics`); `sum(DEFAULT_FEED_ALLOCATION.values())==30`; all 8 roots + 2 source keys present.
- **Dependencies:** none

### Sub-phase 2: Canonical taxonomy in the TS twin
- **Files touched:** `src/lib/feedBuckets.ts` (`DesignBucketId`, `FeedCategoryEnum`, `DESIGN_BUCKETS`, `DESIGN_BUCKET_TO_ENUM`, `PICKER_ROOT_TO_CATEGORY_BUCKET`, `DEFAULT_ALLOCATION_SEGMENTS`), `src/lib/interestVector.ts` (`INTEREST_ROOT_TO_PINNED_KEY` / archetype keys if they gate buckets).
- **What ships:** `DESIGN_BUCKETS` = the 8 roots (each with label + accent color matching onboarding) + youtube/x; `DESIGN_BUCKET_TO_ENUM` is identity for the 8 roots; `PICKER_ROOT_TO_CATEGORY_BUCKET` is identity (no fold); `DEFAULT_ALLOCATION_SEGMENTS` re-summed to 30 and **equal to** the Python `DEFAULT_FEED_ALLOCATION` (Rule 7 twins).
- **Definition of done:** `npm run build` green; `npm test` (feedBuckets suite) asserts the TS default segments equal the Python default allocation (same keys + counts, total 30) and `PICKER_ROOT_TO_CATEGORY_BUCKET` maps each of the 8 roots to itself.
- **Dependencies:** Sub-phase 1 (the Python default is the agreed split; TS mirrors it)

### Sub-phase 3: DB migration + reel-chip label/color parity (`⚠ irreversible` — DB)
- **Files touched:** new `supabase/migrations/0020_taxonomy_8_roots.sql`; `src/types/feed.ts` (`SegmentKey` `:35`), `src/lib/interests.ts` (`SEGMENT_ACCENT_HEX` `:168`), `src/lib/feed/fixtureFeed.ts` (`SEGMENT_LABELS` `:52`), `src/lib/feed/supabaseFeed.ts` (segment join).
- **What ships:** migration adds the 8 root values to the `feed_category` enum (additive `ALTER TYPE … ADD VALUE`), reconciles the reel `segment_slug` enum (`0001_content_schema.sql:14`) + the `segments` table (label + accent_hex rows for the 8 roots), and **backfills** existing `stories.story_segment_slug` from `story_detail_category`/old segment (e.g. `wildcard→arts/culture`, `geopolitics→geopolitics`); the reel chip (`ReelStage.tsx`/`ArticleLayer.tsx` read path) renders the root label + color that **equals** the onboarding chip for that root.
- **Definition of done:** migration applies cleanly on a scratch DB via the IPv4 session pooler (`news20-supabase-ddl-connection`) — NOTE `0019` is taken by another session (entity_reference_images), so use `0020`; `select count(*) from stories where story_segment_slug is null or story_segment_slug not in (<8 roots>)` returns 0 after backfill; a test asserts a `sport` story's chip label+hex == the onboarding `sport` label+hex; `npm test` + `npm run build` green.
- **Dependencies:** Sub-phases 1, 2

### Sub-phase 4: Finish breaking removal in the 3 lagging scripts + cross-surface parity check
- **Files touched:** `scripts/produce_source_reels.py` (`:12,63,67-68,233`), `scripts/e2e/allocate_test_feeds.py` (`_category_for_slot` `:214-216`), `scripts/e2e/drive-profile.ts` (label map `:80`).
- **What ships:** the breaking *allocation/slot* concept is removed from all three scripts (no `feed_slot_kind == "breaking"` reads/writes, no `breaking` label map, no `breaking` slot category); the kept `story_is_breaking` velocity sites are untouched.
- **Definition of done:** `grep -rn "breaking" scripts/` returns only non-allocation hits (velocity/unrelated); an end-to-end smoke (a seeded profile through onboarding → Build-your-30 → reel) shows the **same category label string** for a given root on all three surfaces (e.g. "Geopolitics" everywhere, never "World & Politics" or "wildcard").
- **Dependencies:** Sub-phases 1, 2, 3

## Phase-level definition of done
A given picker root renders the **identical label + accent color** in onboarding, Build-your-30, and the reel chip (proven by a parity test/smoke); `category_for_slug` and the TS twin both classify into the 8 roots with no fold; default allocations total 30 and match across Py/TS; the `0019` migration is applied and every existing story has a valid new `story_segment_slug`; `grep` shows no breaking *allocation* references anywhere; `pytest` + `npm test` + `npm run build` green; `story_is_breaking` velocity signal still computed. Single commit at phase end.

## Out of scope
- The shared pool / demand-driven ingestion / clustering (M2/M3 — already partly shipped; this phase only fixes the taxonomy they read).
- The `podcasts` source axis (UI-only today; left untouched per owner).
- A physical Postgres enum *drop* of the old folded values (`world_politics` etc.) — leave them unused/retained (cheap + reversible), like SP1 did with `breaking`.
- Re-seeding interests for users — separate data task.

## Open questions
1. **The default 30-slot split across 8 topic + 2 source categories** — owner must confirm the per-category counts (e.g. `ai 4, tech 4, geopolitics 4, business 4, politics 2, environment 2, sport 3, arts 3, youtube 2, x 2 = 30`). SP1 should not land until this is set.
2. **`segment_slug` backfill mapping** for existing stories: old `wildcard` → `arts`? old `geopolitics` → `geopolitics` (now its own root) vs split into `geopolitics/politics/environment`? Recommend a deterministic map keyed on `story_detail_category`; confirm the ambiguous `wildcard` and `world_politics` cases.
3. **Retain vs. rename old enum values** (`world_politics`, `tech_science`, `culture`): recommend retain-unused for reversibility.

## Self-critique

**Product lens:** PASS. The "everything matches" unification is the core of the owner's request and serves the MVP's "simple personalization / interest categories at onboarding" — onboarding intent now survives intact to the reel. No out-of-brief feature. The rework's riskiest assumption (clustering thresholds) stays in M3 — this phase is deterministic taxonomy plumbing, correctly sequenced before the ML build reads the categories.

**Engineering lens:** PASS with note. Each DoD is fresh-context checkable (specific `category_for_slug` assertions, a `sum()==30` check, a `select count(*)` after backfill, a label+hex parity test, a `grep`). SP1 (Python) and SP2 (TS) are genuine twins, not the same thing — but SP2 depends on SP1 so the agreed split exists once. SP4 (scripts) is sequenced last and locks nothing premature. **Note:** SP3 expands a Postgres enum + reconciles `segment_slug` + backfills rows — large for one sub-phase; if the executor finds the backfill non-trivial it may split SP3 into "migration" + "backfill+render" and escalate (Rule 12).

**Risk lens:** PASS with flags. **File-boundary:** SP1/SP2/SP4 touch disjoint files; SP3 touches the migration + render files; the only shared dependency is the agreed default split (threaded SP1→SP2). **Reversibility:** SP3 is `⚠ irreversible` (enum + segment backfill on live data) — mitigated by additive `ADD VALUE`, retaining old values unused, and a deterministic re-runnable backfill; take a DB snapshot first. **Test coverage:** SP1/SP2/SP3 each carry an automated assertion (Rule 9); SP4's parity smoke encodes the user-visible intent. **Painting-into-a-corner:** 1→2→3→4 simulated — code classifies into 8 roots (1,2), DB + render follow (3), scripts cleaned + parity proven (4); the backfill in 3 depends on the new enum from 1/2 being agreed, which it is. No corner.

**Irreversible sub-phases:** Sub-phase 3 (`⚠ irreversible` — `feed_category`/`segment_slug` enum expansion + `story_segment_slug` backfill; mitigated by additive ALTERs, retained old values, deterministic re-runnable backfill, pre-snapshot).
