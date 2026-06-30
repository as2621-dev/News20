# Sub-phase 3 — ESCALATION / STOP (no production code written)

**Phase:** phase-fsr-m2-theme-category
**Sub-phase 3:** Wire theme-derived category into ingestion-time tagging
**Outcome:** STOPPED — escalation gate (Open Question 1) tripped. No code written for SP3 (or SP4). Per Rule 12 + the phase's conditional-escalation clause, the orchestrator did NOT invent schema and did NOT commit a partial phase.

## The escalation condition (Open Question 1 / phase-specific note "SP3 conditional escalation")

SP3's recommended wiring is: emit a **depth-0 `story_interests` tag on the category-ROOT interest** so that `ranking.py::assign_category` (unchanged) resolves the story's category from the theme-derived root slug via `category_for_slug` (which round-trips the 8 root slugs identity).

That mechanism requires a **category-root interest id per root** to exist in the taxonomy (`interest_nodes`). The phase explicitly says: *"SP3 must confirm a category-root interest id exists in `interest_nodes` for each of the 8 roots; if not, that's the escalation."*

## Verified facts (against the live taxonomy seed — offline, no DB)

Source of truth for the taxonomy rows: `supabase/seed/interests.sql` (depth-0 roots) + `supabase/seed/interests_picker_topics.sql` (picker leaves) + migrations `0020`/`0021`.

1. **The depth-0 `interests` rows are the OLD pre-8-root slugs**, not the 8 picker roots:
   `supabase/seed/interests.sql` lines 17-29 insert depth-0 rows with slugs:
   `world, business, tech, sport, health, entertainment, climate, lifestyle, crypto, science`.

2. **The 8-root taxonomy (`ai, geopolitics, business, environment, politics, tech, sport, arts`) lives ONLY in the `segments` table** (migration `0021_taxonomy_8_roots_backfill.sql`, the `segments` upsert) and in **interest_slug namespacing** of depth-1 picker leaves — e.g. `interests_picker_topics.sql` inserts `ai.data-center-buildout` with **parent `tech`**, `geopolitics.russia-sanctions` with parent **`world`**, `environment.solar` with parent **`climate`**, `politics.national-elections` with parent **`world`**. There is **NO depth-0 interest row** whose `interest_slug` is `ai`, `geopolitics`, `environment`, `politics`, or `arts`.

3. **Coverage of the 8 roots as interest nodes:**
   - EXIST as interest rows (slug round-trips in `category_for_slug` identity): **`business`, `tech`, `sport`** → 3 of 8.
   - MISSING as interest rows: **`ai`, `geopolitics`, `environment`, `politics`, `arts`** → **5 of 8**.

4. **`assign_category` drops orphan tags.** `agents/pipeline/stages/ranking.py` lines 834-841:
   ```python
   resolvable = [
       (interest_id, match_depth, interest_nodes[interest_id].interest_slug)
       for interest_id, match_depth in story_tags.items()
       if interest_id in interest_nodes        # <-- orphan tag silently excluded
   ]
   if not resolvable:
       return DEFAULT_CATEGORY
   ```
   So a synthetic depth-0 tag pointing at a non-existent `ai`/`geopolitics`/`environment`/`politics`/`arts` root interest id is **filtered out**, and the story falls to `DEFAULT_CATEGORY = "arts"`. That would silently mis-categorize **5 of the 8 categories** — strictly worse than the bug M2 is meant to fix.

5. **In-memory flow confirmed** (so the wiring seam itself is plausible, the BLOCKER is purely the missing root nodes): `agents/pipeline/daily_batch.py` loads `interest_nodes` ONCE and passes the SAME map + the in-memory `story_interest_tags` to ingest, `cap_stories_per_category`, `_produce_story_pool`, and `assemble_daily_feeds` → `assign_category` (line ~904). Tags are NOT round-tripped through the DB between ingest and rank. So if the 8 root nodes existed, a depth-0 root tag WOULD reach `assign_category` correctly.

## Why this is a STOP, not a "make it work"

To make the recommended (a) mechanism viable, one of these is required — all out of scope / forbidden:

- **(1) Mint 8 depth-0 root interest rows** (a seed row + likely a migration for the new root slugs `ai/geopolitics/environment/politics/arts`). This is a **schema/seed change**, explicitly listed in the phase **Out of scope** ("Any schema/migration change … if it can't, that's an escalation") and forbidden by the orchestrator instruction ("do NOT invent schema").
- **(2) Synthesize in-memory category-root `InterestNode`s and mutate the shared `interest_nodes` map** at the tag site. This is taxonomy-inventing, and to be correct it must inject the SAME synthetic roots at EVERY `assign_category` call site (`daily_batch`, `feed_assembly`, `produce_caps`) — cross-cutting edits well beyond SP3's allowed files (`interest_keyed_pipeline.py`, `models.py`, the pipeline test). Picking a stable synthetic id, guaranteeing no collision with real interest ids, and threading it through three consumers is exactly the "inventing schema" the gate warns against.

Mechanism (b) from Open Q1 (carry the `FeedCategory` directly on the tag payload and have categorization read it) requires **changing `assign_category`** (it reads slugs, not categories) — the phase forbids touching `assign_category` logic.

There is no in-scope, no-schema, offline-correct wiring for the 5 missing roots. Per Rule 12 (fail loud, don't silently mis-wire) the correct action is to escalate.

## Recommended resolution (for the next planning turn — orchestrator's call, not done here)

The cleanest fix is a tiny, additive **seed/migration sub-phase that mints the 8 depth-0 root interests** (slugs `ai, geopolitics, business, environment, politics, tech, sport, arts`, `depth_level=0`, `parent NULL`, idempotent `on conflict (interest_slug) do nothing`), and **re-parents the picker leaves** (`ai.* → ai`, `geopolitics.* → geopolitics`, `environment.* → environment`, `politics.* → politics`, plus mapping `world`/`climate` legacy roots) so each leaf's root segment is a real interest node. THEN SP3's recommended depth-0-root-tag wiring becomes viable with zero `assign_category` change. This is a schema change → it must be planned as its own milestone/sub-phase (M2.5 or folded into M5/M6 onboarding-taxonomy work), not smuggled into M2's SP3.

Alternatively, the owner may decide the category signal should ride on the tag's **slug** without a root node — but that requires an `assign_category` contract change, which is a deliberate design decision, not an M2 sub-phase tweak.

## Status of SP1 + SP2 (complete, correct, UNCOMMITTED)
- SP1: `agents/pipeline/theme_category.py` + `tests/agents/pipeline/test_theme_category.py` — 16 passed. Pure `category_for_themes(themes) -> FeedCategory`, UPPERCASE-keyed whitelist, deterministic hit-count→priority tiebreak, fail-loud DEFAULT + warning.
- SP2: `agents/ingestion/models.py` (`CandidateStory.candidate_themes`), `agents/ingestion/adapters/gdelt_bigquery.py` (`_BATCH_SQL` selects `V2Themes`, `_parse_v2_themes`, `_rows_to_candidates`), `tests/agents/ingestion/conftest.py` + `tests/agents/ingestion/test_gdelt_bigquery_adapter.py` — 29 passed (broader ingestion 161 passed).

These remain in the working tree, uncommitted, pending the escalation decision (the orchestrator does not commit a partial phase).

## Definition of done: FAIL (escalated — SP3 not implemented; gate tripped)
