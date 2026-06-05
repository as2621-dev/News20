# Phase 5a: Build-your-30 allocator + entity-aware ranking

**Milestone:** M5 — Two-axis personalization (sources + control surface)
**Status:** Not started
**Estimated effort:** L (largest M5 phase — allocator rewrite + scoring + live migration + e2e in one phase, per owner's "both together" call 2026-06-05; flagged at risk of overrunning one `/run-phase` session — see Self-critique)

> **Supersedes** `plans/phase-5e-control-surface.md` and `reference/control-surface-spec.md` (the master-dial + 30-cell-ribbon design). Owner replaced that with the **"Build your 30, in order"** screen (2026-06-05): one ordered list, explicit per-category slot counts, manual sequence. This phase builds that screen's **backend** + folds the already-captured **entity follows** (phase-5, migration 0007) into ranking. The original narrow framing (`phase-5a-entity-ranker-consumption`) is expanded here.

## Goal
A signed-in user's `daily_feeds` is assembled from **user-set per-category slot budgets + manual sequence** ("Build your 30"), each category's slots filled by our entity-aware Score — so a user who follows **Nvidia** sees Nvidia stories rank up **within their category**, and the 30 slots honor exactly the counts and order the user arranged.

## Decisions locked (owner, 2026-06-05)
- **Two-layer model.** Layer 1 = *allocation* (user sets per-category counts + order). Layer 2 = *scoring* (our Score picks which stories fill each category's slots). Entity follows live in Layer 2.
- **Entity signal = score-bonus**, allocator-agnostic (a term on the per-(user, story) Score), **not** a reserved slot tier — the user already reserves slots by category.
- **Match surface = label words + company ticker.** Match `entity_label` as whole words in the story **title**, plus `entity_ticker` only for `entity_kind = 'company'` (word-bounded). Residual short-ticker false-positive risk (`AI`, `ON`, `ALL`) documented + tested.
- **Bucketing = exactly one best-fit category** per story (clean 30-slot accounting; no duplicates; budgets exact).
- **Category list = the 8 on the screen, as drawn:** `Breaking News · World & Politics · Tech & Science · YouTube · Markets · Sport · X · Culture`. The 13 seeded interest slugs map up into these 8 (map below).
- **DB scope = include live `0007` + new `0008` apply + real e2e.**
- **Sources soft-bias:** `YouTube` and `X` are source categories; source ingestion is `phase-5d` (not built), so they contribute **zero** items today and their budgeted slots **roll into the topic categories by sequence** (feed still totals 30). Built source-aware so 5d slots in later with no allocator change.

### Working slug → category map (3 flagged for review — see Open questions)
| Screen category | Filled from | Notes |
|---|---|---|
| Breaking News | *(tier)* top-Importance across all categories | user-budgeted count; not a slug bucket |
| World & Politics | `geopolitics`, `world`, `climate`* | |
| Tech & Science | `tech`, `science`, `health`* | |
| Markets | `business`, `markets`, `crypto` | |
| Sport | `sport` | |
| Culture | `entertainment`, `lifestyle`, `wildcard`* | |
| YouTube | *(source axis)* | empty until phase-5d |
| X | *(source axis)* | empty until phase-5d |

`*` = ambiguous slug, default chosen, confirm before run-phase: `climate`→World&Politics, `health`→Tech&Science, `wildcard`→Culture.

## Sub-phases

### Sub-phase 1: Migration `0008_feed_allocation` + live apply (0007 + 0008 + seeds)
- **Files touched:** `supabase/migrations/0008_feed_allocation.sql` (new); apply `0007` + `0008` + `supabase/seed/entities.sql` to the live DB.
- **What ships:** an additive, forward-only migration adding — enum `feed_category` (the 8 keys: `breaking`, `world_politics`, `tech_science`, `youtube`, `markets`, `sport`, `x`, `culture`); table `user_feed_allocation` (`follow_user_id uuid → auth.users(id) on delete cascade`, `allocation_category feed_category`, `allocation_slot_count int not null check (0 ≤ count ≤ 30)`, `allocation_sort_order int not null`, `allocation_updated_at timestamptz default now()`, PK `(follow_user_id, allocation_category)`) with **owner-all RLS** mirroring `0005`/`0007` exactly (`using (follow_user_id = auth.uid()) with check (...)`); index `idx_user_feed_allocation_user`. Then migrations `0007` (entities) + `0008` are pushed to the **live** Supabase via the IPv4 session pooler (`db push --db-url`, aws-1-us-east-1 — direct host is IPv6-only, per `news20-supabase-ddl-connection`), and `entities.sql` (248 rows) is seeded.
- **Definition of done:** `select count(*) from entities` returns 248 on the live DB; `\d user_feed_allocation` shows the table with the `feed_category` enum + owner-all policy; an authenticated insert of a `(user, 'tech_science', 5, 2)` row succeeds and a **second** user's `select` returns **zero** of the first user's rows (RLS allow/deny proven live); the sum-of-counts CHECK is exercised. Captured psql/SQL output in the execution report.
- **Dependencies:** none.
- **⚠ irreversible** — forward-only additive migration applied to the live DB; no down migration. Confirm a disposable/backed-up DB state first.

### Sub-phase 2: Entity hydration + entity-aware Score + story→category classifier
- **Files touched:** `agents/pipeline/categories.py` (new — the 8-category enum + slug→category map + best-fit rule, single source of truth), `agents/pipeline/stages/ranking.py` (entity bonus + `score_and_classify_for_user`), `agents/pipeline/daily_batch.py` (hydrate `user_entity_follows ⋈ entities` + `user_feed_allocation`), `agents/pipeline/orchestrator.py` (`ActiveUserFeedInputs` gains `followed_entities` + `category_allocation`), `tests/agents/pipeline/test_ranking.py` (extend).
- **What ships:** (a) a `FollowedEntity` model (`entity_id`, `entity_label`, `entity_ticker`, `entity_kind`, `follow_weight`, `follow_path`) and loader hydration of a user's entity follows (join to `entities` for label/ticker/kind) + their `user_feed_allocation` rows; (b) `entity_title_match(story, followed_entities)` — whole-word `entity_label` match in the title, plus word-bounded `entity_ticker` match **only** for `entity_kind == 'company'`; (c) the Score gains an additive **EntityBonus** term: `EntityBonus = normalized_follow_weight × ENTITY_BONUS_WEIGHT` applied to a story that matches a followed entity (follow_weights max-normalized per user, mirroring `normalize_affinities`; `ENTITY_BONUS_WEIGHT` a first-draft config constant ≈ 0.3, confirmed at the sim/e2e run); (d) `assign_category(story, tags) → feed_category` implementing the single best-fit rule (the category of the story's lowest-`match_depth` interest tag; tiebreak by interest sort order) using the map in `categories.py`; (e) `score_and_classify_for_user(...) → {feed_category: [ScoredCandidate]}` — entity-aware candidates bucketed by the 8 categories.
- **Definition of done:** unit tests prove — **happy:** for a Nvidia follower, "Nvidia Q3 earnings beat" scores **strictly higher** than the same story with the entity bonus removed, and lands in `markets` (best-fit); **false-positive guard:** ticker `AI` (a non-company entity) does **not** boost a generic "AI" headline, and `word_match` does not fire on substrings ("Meta" ∉ "metabolism"); **edge:** a follower whose entities match no story gets byte-identical scores to the no-entity baseline; **custom > seed:** a `custom`-source follow yields a larger bonus than a `seed`-source follow on the same story. DB hydration tested against a **mocked** client (CLAUDE.md boundary mandate).
- **Dependencies:** Sub-phase 1 (the `user_feed_allocation` + `entities` contracts it hydrates).

### Sub-phase 3: Category-budget + sequence allocator (feed_assembly rewrite)
- **Files touched:** `agents/pipeline/feed_assembly.py`, `tests/pipeline/test_feed_assembly.py`.
- **What ships:** the allocator rewritten from affinity-proportional buckets to **user-set per-category budgets + manual sequence**: it reads each user's `category_allocation` (count + sort_order per the 8 categories), fills each category's slots from SP2's `score_and_classify_for_user` buckets (top-Score, entity-aware, qualifying ≥ T), in the user's **sequence order**; `breaking` is filled by top-Importance across all categories (the tier, now user-budgeted); **source categories (`youtube`/`x`) contribute zero** and their unfilled budget **soft-rolls** into the remaining topic categories by sequence so the feed still totals 30; preserves §3.8 don't-repeat and within-feed dedup; **default allocation** for a user with no `user_feed_allocation` rows (balanced fallback: `breaking 4` + even split across non-source categories with available stories) so pre-screen users still get a feed.
- **Definition of done:** pure-function unit tests prove — given an allocation `{breaking 2, world_politics 4, tech_science 5, markets 4, sport 3, culture 3, youtube 6, x 3}` and a story pool, the assembled feed has **exactly** 2/4/5/4/3/3 topic slots (subject to availability), the 9 source slots **rolled into topics** so `len(feed) == 30`, slots ordered by the user's sequence, **no duplicate** story, prior-feed stories excluded; a Nvidia-followed story appears within `markets`/`tech_science`; the no-allocation user gets the balanced default; a category with no eligible stories yields its slots to the next category (not a gap).
- **Dependencies:** Sub-phase 2.

### Sub-phase 4: Live e2e + offline sim + spec updates + supersede 5e
- **Files touched:** `agents/pipeline/sim/ranking_sim.py` + `agents/pipeline/sim/world.py` (extend), a live e2e check (script or marked test), `reference/ranking-spec.md` (new § — entity bonus + category-budget allocation), `reference/control-surface-spec.md` + `plans/phase-5e-control-surface.md` (SUPERSEDED banners pointing here).
- **What ships:** (a) a real end-to-end run against the **live** DB — seed a test user with a `user_feed_allocation` + a `user_entity_follows` (Nvidia), run the allocator path, and assert the produced `daily_feeds` honors the per-category counts + sequence (source budgets rolled into topics) with the Nvidia story placed within its category **above** an equivalent non-followed story; (b) the offline sim extended to assert the same ordering invariant deterministically (no live deps); (c) `ranking-spec.md` documents both new mechanisms as the source of truth; (d) `control-surface-spec.md` + `phase-5e-control-surface.md` carry a clear **SUPERSEDED-by-phase-5a** banner.
- **Definition of done:** the live e2e produces a `daily_feeds` feed for the seeded user whose per-category slot counts + order match the seeded allocation **and** in which the Nvidia story outranks its non-followed twin (captured output in the execution report); the offline sim asserts the same and passes in CI with no network; `ranking-spec.md` has the entity-bonus formula + category-budget algorithm; both superseded docs link here.
- **Dependencies:** Sub-phases 1, 2, 3.

## Phase-level definition of done
Running the allocator against the **live** DB for a seeded user (a `user_feed_allocation` + a Nvidia `user_entity_follows`) produces a `daily_feeds` feed whose **per-category slot counts and order match the user's allocation** (source-category budgets soft-rolled into topics, feed totals 30, no duplicates), with the **Nvidia story ranked within its category above an equivalent non-followed story**; migrations `0007` + `0008` are live and `entities` is seeded (248 rows); the offline sim asserts the entity-boost + budget invariants with no network; and `ranking-spec.md` documents both mechanisms while `phase-5e` + `control-surface-spec.md` are marked superseded.

## Out of scope
- The **frontend** "Build your 30" screen itself (owner is building the UI). This phase ships the **backend contract** it writes to (`user_feed_allocation` shape + the 8 `feed_category` keys); the e2e seeds rows directly via service-role, not through the UI.
- **Source ingestion** (YouTube/X content) — `phase-5d`. Source categories are budgeted-but-empty here.
- **Learned ordering** from engagement — `phase-6b`. Sequence is manual in v1.
- **Entity tagging via NLP/licensed data** (master-plan "out of scope" line 146) — we use lightweight title/ticker matching only.
- Archetype mapping / recommendations (`phase-5c`), the source data model (`phase-5b`).

## Open questions
1. **Three ambiguous slug mappings** — confirm `climate`→World&Politics (vs Tech&Science), `health`→Tech&Science (vs World&Politics/Culture), `wildcard`→Culture (vs distribute). Defaults chosen; trivially editable in `categories.py`.
2. **`ENTITY_BONUS_WEIGHT` magnitude** (≈0.3 first draft) — tune at the SP4 sim/e2e so an entity follow meaningfully lifts a story without drowning Affinity×Depth. Same "confirm at manual run" pattern as the existing Score constants.
3. **Default allocation for pre-screen users** — confirm the balanced fallback (`breaking 4` + even split) vs. retaining the old affinity-proportional behavior as the no-allocation default.
4. **Migration renumber** — this phase claims `0008` (applied live). `phase-5b`'s planned source-data-model migration is renumbered to `0009` in the master plan; confirm no other consumer hard-codes `0008` for sources.
5. **Breaking as a budgeted category** — confirm breaking should be user-budgetable (the screen shows "Breaking News − 2 +") rather than a fixed ~4-slot preempt; this phase makes it user-set with a default of 4.

## Self-critique

**Product lens:** PASS. Traces to the brief's MVP "Personalization (simple): interest categories at onboarding + prioritize the categories the user engages with. No multi-signal ML." — the "Build your 30" allocator is exactly user-driven category prioritization, still heuristic (no ML). It directly resolves brief Open-Q3 ("World vs my field": the user themselves dials Breaking/World up or their niche category up). Entity follows extend "simple categories" slightly but were already captured in shipped phase-5 and remain heuristic — no scope creep beyond M5's locked two-axis model. Serves the 90-day metric (a feed arranged to the user's own priorities should lift the week-4 return rate). No feature outside the brief. Finding addressed: the riskiest assumption (digest quality) is **not** this phase — correct, it was gated at M0; this is M5 personalization, properly sequenced after.

**Engineering lens:** PASS with flags. (a) **Scope/size flag (Rule 12):** owner explicitly chose "both together," and this is a genuinely L phase (live migration + scoring rewrite + allocator rewrite + e2e). It may exceed one `/run-phase` session — if a sub-agent stalls, run SP1→SP2→SP3 first, then SP4 separately. Surfaced, not hidden. (b) All DoDs are fresh-context-verifiable (live row counts, RLS allow/deny, strict score inequality, exact per-category slot counts) — none are "works end-to-end" hand-waves. (c) **SP4 cements** the spec last, after the engine is proven (correct ordering — doesn't lock the formula before the sim tunes the weight). (d) No two sub-phases are secretly the same: SP2 = scoring/classification (ranking.py + loader), SP3 = budget allocation (feed_assembly.py) — distinct files, distinct concerns.

**Risk lens:** PASS with flags. **File boundaries:** clean — `ranking.py`/`daily_batch.py`/`orchestrator.py`/`categories.py` (SP2), `feed_assembly.py` (SP3), docs+sim (SP4), migration (SP1); **no two sub-phases edit the same file**, so worktree parallelism is safe (dependencies still force SP1→SP2→SP3→SP4 ordering). **Test coverage:** every sub-phase DoD carries a test (SP1 live RLS allow/deny, SP2 happy/false-positive/edge per Rule 9, SP3 allocation invariants, SP4 sim + live e2e). **Reversibility:** SP1 is `⚠ irreversible` (forward-only live migration) — flagged for extra care + disposable-DB confirmation. **Painting-into-a-corner:** simulate 1→2→3→4 — SP1 lays schema, SP2 produces entity-aware category buckets against that schema, SP3 allocates from those buckets honoring budgets, SP4 proves + documents + supersedes. SP3 works given SP2's output shape; SP4 needs all three. No corner. **Conflicts surfaced (Rule 7):** supersedes 5e/control-surface-spec (owner-confirmed); migration renumber of 5b→0009; the 8-category taxonomy amends decision C1's locked-8 (owner chose screen-as-drawn).

**Irreversible sub-phases:** Sub-phase 1 (forward-only `0007`+`0008` live apply + entity seed — `⚠ irreversible`).
