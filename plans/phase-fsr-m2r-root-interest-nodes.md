# Phase FSR-M2R: Root interest nodes + picker-leaf re-parent

**Milestone:** M2R — taxonomy foundation (unblocks M2 theme→category tagging)
**Status:** Shipped
**Estimated effort:** S

## Goal
Make all **8 top-level topic roots** (`agents/pipeline/categories.py::TOPIC_CATEGORIES` = `ai · geopolitics · business · environment · politics · tech · sport · arts`) exist as **depth-0 interest nodes**, and re-home every depth-1 picker leaf under its true root, so M2-SP3 can tag a story's category onto a real interest node that `ranking.py::assign_category` will resolve (instead of silently dropping it).

## Why this phase exists
M2 is blocked: `assign_category` (`agents/pipeline/stages/ranking.py` ~L834-841) DROPS any `story_interests` tag whose `interest_id` is not a known interest node. Only **3 of 8** roots existed as interest rows (`business`, `tech`, `sport` — `supabase/seed/interests.sql`). The other 5 (`ai`, `geopolitics`, `environment`, `politics`, `arts`) lived only in the `segments` table and in the slug-namespacing of depth-1 picker leaves (`interests_picker_topics.sql`), whose leaves were parented to **legacy** depth-0 roots (`ai.*`→`tech`, `geopolitics.*`/`politics.*`→`world`, `environment.*`→`climate`, `arts.*`→`entertainment`). So a theme-derived depth-0 root tag for any of the 5 missing roots collapsed to `DEFAULT_CATEGORY` ("arts") — strictly worse than the bug M2 fixes. (See `.agents/execution-reports/phase-fsr-m2-theme-category-sub-3.md` — the M2-SP3 escalation that this phase resolves.)

## The migration — `supabase/migrations/0023_root_interest_nodes.sql`
1. **MINT** the 8 depth-0 root interest nodes (`depth_level=0`, `parent NULL`, `interest_segment_slug` = the matching `segments` row from 0021, `interest_slug` == the `FeedCategory` key) via `insert … on conflict (interest_slug) do nothing`. The 3 pre-existing roots (`business`/`tech`/`sport`) are **reconciled, not duplicated and not overwritten** (`do nothing` preserves their Phase-1e labels/segment/sort); only the 5 missing roots are inserted.
2. **RE-PARENT** every depth-1 picker leaf to its true root using the slug's root segment — the same logic as `categories.category_for_slug`: `update interests leaf … set parent_interest_id = root.interest_id … where root.interest_slug = split_part(leaf.interest_slug,'.',1) and leaf.depth_level = 1 and leaf.parent_interest_id is distinct from root.interest_id`. Re-homes `ai.*`→`ai`, `geopolitics.*`→`geopolitics`, `environment.*`→`environment`, `politics.*`→`politics`, `arts.*`→`arts` in one statement; inert for `business`/`tech`/`sport` leaves already parented correctly; **depth-2 leaves untouched** (they keep their depth-1 parent).

**Additive · idempotent · non-destructive:** never changes `interest_id`/`interest_slug`/`depth_level` (only the parent pointer), so every `user_interest_profile` / `story_interests` FK (which references `interest_id`) stays intact. The `do nothing` + `is distinct from` guards make a re-apply a byte-for-byte no-op. Legacy depth-0 roots (`world`, `climate`, `entertainment`, `health`, `lifestyle`, `crypto`, `science`) are left in place.

## The category→root-interest mapping (for M2-SP3)
`agents/pipeline/categories.py::root_interest_slug_for_category(category) -> str | None`: the stable accessor SP3 uses to know which depth-0 interest node to tag a story onto. Returns the category key itself for the 8 **topic** roots (identity — `ai`→`"ai"`, … — the inverse of `category_for_slug` on a bare root slug), and `None` for the 2 **source-axis** categories (`youtube`/`x`), which are follow-gated and have no interest node. Consistent with `category_for_slug`; round-trip enforced by test.

## Definition of done
- All 8 roots exist exactly once, `depth_level=0`, `parent NULL`. ✅
- Every depth-1 picker leaf's parent slug == its own slug-root (clean re-parent); depth-2 leaves unmoved. ✅
- Idempotent re-apply is a no-op (no rows added/removed/changed). ✅
- No orphaned interests; FK integrity holds; an existing `user_interest_profile` row referencing a re-parented leaf still resolves (under its new root). ✅
- Structural parse tests + ephemeral-PG apply all green (`tests/supabase/test_migration_0023_root_interest_nodes.py`, 9 passed, ephemeral test ran live on PG16 — not skipped).

## Out of scope
- M2's tagging **wiring** (`interest_keyed_pipeline.py` / SP3 ingest emit of the depth-0 root tag) — a separate phase. This phase only builds the data foundation + the accessor SP3 will call.
- M5's user-row interest collapse / onboarding-roots work (`phase-fsr-m5-onboarding-roots-and-interest-collapse.md`).
- Any change to `assign_category` logic — forbidden; only the data it resolves against changes.
- Deleting/renaming legacy interests.

## Rollback / down note
Forward-only repo, so down is by **data**, documented inline in `0023`'s header:
1. Re-parent leaves back to legacy roots (`ai.*`→`tech`, `geopolitics.*`/`politics.*`→`world`, `environment.*`→`climate`, `arts.*`→`entertainment`).
2. Delete the **5 newly-minted** roots IFF childless (`ai`/`geopolitics`/`environment`/`politics`/`arts`); never delete `business`/`tech`/`sport` (they pre-existed).
Down is only safe **before** M2-SP3 has written profile/story rows referencing the new root ids.

## Reconciliation surprises
None blocking. Every dotted leaf slug's root segment maps cleanly to exactly one of the 8 roots (verified across both seed files — no ambiguous leaf, so no escalation). Note: the Phase-1e legacy `tech.ai` / `tech.ai.llms` stay under `tech` (their slug-root is `tech`), distinct from the picker's `ai.*` root — correct per `category_for_slug`.

## LIVE-E2E residual
The ephemeral-PG apply ran live in-sandbox (PG16 via `pg_virtualenv`), covering the full DoD. The only residual is applying `0023` against the real Supabase instance via the production session-pooler apply path (the standing residual for every migration in this repo) — deferred, not faked.
