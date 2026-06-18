# Ranking spec — per-user personalized feed (M1)

> **⚠ Rework banner (2026-06-18):** the **shared-pool rework** changes the layers *around* this spec — see `reference/shared-pool-pipeline.md`. It supersedes the candidate-generation half of §2 (per-user fallback-tree search → demand-sized shared pool), enriches the §1 `Importance` term into the full `story_importance` (E1), adds an MMR diversity term, and retires the breaking tier (→ velocity signal). The per-user Score (§1), EntityBonus (§3a.1), and category-budget allocation (§3a.2) below are **preserved**.

**Status:** Active (M1). **Owner decision (2026-05-30):** the feed must feel built-for-you from the first 10–50 users, even if some reels are generated per-user. Heuristic, **not ML** (<50 users, no training data). Referenced by `plans/phase-1d-daily-content-pipeline.md` (implements it) and `plans/phase-1e-auth-onboarding-interest-profile.md` (the schema it reads/writes).

This is the contract for: how a story is scored for a user, how the fallback tree search finds candidates, how the ~30-slot daily feed is allocated, and how the interest profile updates from engagement. It supersedes the master-plan's single "world tier + niche tier" framing (Open Q2/Q3) — see §6.

---

## 0. The three layers (mirrors how TikTok/IG/FB do it, stripped down)

1. **Candidate generation** = the fallback tree search (§2). Pulls a pool of fresh stories tagged to the user's interests.
2. **Ranking** = the per-(user, story) `Score` (§1).
3. **Re-ranking / allocation** = the ~30-slot quota assembly with breaking preempt + diversity + exploration (§3).

Layers 1–3 run in the **Python/Trigger.dev daily batch** and are precomputed into the `daily_feeds` table (§5). The client read is a trivial indexed select — there is **no live ranking RPC** (static-export app has no server runtime; the algorithm is a heavy multi-pass best unit-tested in Python).

---

## 1. Story score for a user

```
Score(user, story) = (Affinity × DepthMatch) · 0.5  +  Importance · 0.3  +  Freshness · 0.2
```

| Term | Range | Source | Meaning |
|------|-------|--------|---------|
| **Affinity** | 0–1 | `user_interest_profile.profile_weight` on the matched interest node (normalized) | how much the user cares about this interest |
| **DepthMatch** | leaf 1.0 / parent 0.6 / grandparent 0.3 | `story_interests.story_interest_match_depth` (0/1/2) | how *specifically* the story hits the user's node — this is what makes a small Mumbai-Indians item beat a generic big story for a Mumbai-Indians fan |
| **Importance** | 0–1 | normalized `stories.story_outlet_count` + source/outlet rank | intrinsic magnitude; many outlets fast ⇒ breaking |
| **Freshness** | 0–1 | exponential decay on `stories.story_first_reported_utc`, **~24h half-life** | recency |

**Weights** start at `α=0.5, β=0.3, γ=0.2` — affinity-dominant on purpose so niche-but-small news surfaces. Weights are config constants, tunable; do not hardcode in scattered places.

**Threshold `T`** — the minimum Score for a story to be "good enough" to fill a slot (and to stop the fallback climb, §2). `T` is a single config constant; "what counts as a good story" == `Score ≥ T`, not a second metric.

---

## 2. Fallback tree search (candidate generation, per user, per followed leaf)

For each interest the user follows, walk **down→up** the taxonomy until the feed budget for that interest is met:

1. Search/score stories at the **leaf** node (e.g. `sport.cricket.india`).
2. If no story there clears `Score ≥ T`, **fall back to the parent** (`sport.cricket`), then the **grandparent** (`sport`).
3. **Stop conditions:**
   - a qualifying story (`Score ≥ T`) is found, OR
   - the climb hits a **`strict`** node (`user_interest_profile.profile_is_strict = true`) — "just give me cricket, nothing broader" — fallback halts there, no upward broadening, no exploration for that interest, OR
   - the grandparent (depth-0 category) is reached.

Ancestor tagging (set at ingest, §`story_interests`) means a leaf-matched story already carries rows for its parent and grandparent with the right `match_depth`, so a broad follower catches niche stories *for free* at the lower DepthMatch (0.6 / 0.3).

---

## 3. Allocation — assembling the ~30-slot daily feed (per user)

> **⚠ Superseded for signed-in users by §3a (phase-5a, 2026-06-05).** Steps 2–6 (the affinity-proportional split) and step 7 (auto-exploration) are replaced by the user-set "Build your 30" category budgets; §3.1 breaking becomes a user-budgeted tier; §3.8 don't-repeat + the dedup/sparse-fallback rules are **preserved**. This section is retained as the M1 design history. See §3a for the active model.

Let `N ≈ 30` (the per-user feed budget). Do **not** take a global top-30 (that starves niche users). Instead:

1. **Breaking tier (~4 slots, preempt).** Reserve the top ~4 slots for the highest-`Importance` stories tagged to *any* node the user follows. A live spike (the "plane crash they follow") lands here and can grow its share — breaking preempts the proportional split.
2. **Proportional split of the remaining ~26** across the user's followed interests **∝ their normalized `profile_weight`**.
3. **Floor:** every followed *leaf* with ≥1 qualifying story (`Score ≥ T`, via §2) gets **at least 1** slot.
4. **Cap:** no single interest exceeds **~40%** of the feed — UNLESS the user is single-interest or marked `strict` (then it may fill its full budget).
5. **Fill** each interest's bucket from its top-`Score` candidates.
6. **Redistribute** any unfilled slots (interest ran dry) to the next-highest-Affinity interest.
7. **Exploration (~10%).** Reserve ~10% of slots for adjacent/new interests (siblings/parents of followed nodes) so the profile keeps learning. `strict` interests are excluded from contributing exploration slots.
8. **Don't-repeat.** Exclude any `feed_story_id` already present in this user's **prior `daily_feeds`** (and optionally any story with a `complete` `player_signals` row). No dedicated "seen" table in M1 — derived from `daily_feeds` + signals.

Each written `daily_feeds` row records `feed_position` (01..N), `feed_score`, `feed_matched_interest_id`, and `feed_slot_kind ∈ {breaking, interest, exploration}`.

**Sparse/empty-profile fallback:** an un-onboarded or near-empty profile falls back to a recency/importance-ordered feed (no crash, no empty feed).

---

## 3a. Phase-5a — "Build your 30" allocation + entity-aware ranking (SUPERSEDES §3's split; RETIRES §3.7)

**Status:** Active (M5, phase-5a, 2026-06-05). This section is the source of truth for two shipped mechanisms: the **EntityBonus** score term and the **category-budget + manual-sequence** allocator. It **supersedes** §3's affinity-proportional split (steps 2–6) and **retires** the §3.7 auto-exploration tier — see the conflict note at the end.

The model is **two layers** (owner, 2026-06-05):

- **Layer 1 — allocation:** the user sets, per screen category, how many of their 30 slots it gets (`user_feed_allocation.allocation_slot_count`) and where it sits in the manual sequence (`allocation_sort_order`). The user owns breadth — there is no algorithm-chosen proportional split or exploration reserve.
- **Layer 2 — scoring:** the per-(user, story) Score (§1) — now entity-aware — picks **which** stories fill each category's slots.

### 3a.1 EntityBonus (the entity follow as an additive score term)

A user's followed entities (`user_entity_follows ⋈ entities`, migration 0007) add an **additive** term to the Score of a story whose title matches a followed entity:

```
EntityBonus = normalized_follow_weight × ENTITY_BONUS_WEIGHT
Score_entity-aware = Score(§1) + EntityBonus
```

| Element | Rule |
|---|---|
| **Match surface** | `entity_label` matched as **whole words** in `stories.canonical_title` (case-insensitive, `\b…\b`, so "Meta" ∉ "metabolism"), PLUS `entity_ticker` matched as a whole word **only when `entity_kind = 'company'`** (the company-ticker gate — a non-company "ticker" never fires on a generic headline word). |
| **Residual risk** | a *company* whose ticker is a common English word (`AI`, `ON`, `ALL`) still matches that standalone token — owner-accepted, documented + tested; a short-common-word stoplist is the cheap fix if it shows noise. |
| **`normalized_follow_weight`** | the user's `follow_weight` **max-normalized** across their follow set (mirrors `normalize_affinities` — the strongest follow → 1.0). |
| **Source weighting (`custom > more > seed`)** | the DB stores `follow_weight = 1.0` for every source (0007); the loader multiplies by `FOLLOW_SOURCE_WEIGHT` (`seed 1.0`, `more 2.0`, `custom 3.0`) at hydration time, so a custom follow normalizes higher than a seed follow. |
| **Multiple matches** | when several distinct follows match one story, the **strongest** (max normalized weight) wins — the bonus is one additive lift, **not** a sum (a story cannot leapfrog by matching many follows). |
| **Identity dedup** | one logical entity reachable via several follow paths (e.g. Nvidia under AI-hardware vs Business-earnings — multiple `entities` rows sharing label+ticker+kind) is bonused **once** per story (dedup on `(label, ticker, kind)`, highest weight kept). |
| **`ENTITY_BONUS_WEIGHT`** | `0.3` (in `agents/pipeline/stages/ranking.py`, alongside α/β/γ). Confirmed at the phase-5a sim/e2e: a `0.3` lift cleanly reorders a followed story above an otherwise-identical twin within its category (e.g. twin base `0.81` → Nvidia `1.11`) without drowning the Affinity×Depth base. Single config constant; allocator-agnostic. |

The bonus is **allocator-agnostic** — it is a term on the Score, not a reserved slot tier. The user already reserves slots by category (Layer 1); the bonus only changes the **order within** a category.

### 3a.2 Category-budget + manual-sequence allocation

Let `total_target = min(Σ allocation_slot_count, 30)`. The allocator fills the feed from the **8 screen categories** (`agents/pipeline/categories.py`, mirroring the `feed_category` enum, migration 0008):

```
Breaking News · World & Politics · Tech & Science · YouTube · Markets · Sport · X · Culture
```

The 13 seeded interest slugs map up into the 5 **topic** categories (`SLUG_TO_CATEGORY`): World&Politics ← `world`/`geopolitics`/`climate`; Tech&Science ← `tech`/`science`/`health`; Markets ← `business`/`markets`/`crypto`; Sport ← `sport`; Culture ← `entertainment`/`lifestyle`/`wildcard`. Each story classifies into **exactly one** best-fit category (the lowest-`match_depth` tag's root slug; slug tiebreak), for clean 30-slot accounting (no duplicates, budgets exact).

The passes:

1. **Breaking is a budgeted tier, not a slug bucket.** Fill the user's `breaking` budget from the **top-`Importance`** candidates across all topic buckets; remove each chosen story from its topic bucket (no double-placement). (Breaking is now **user-budgetable** — the screen shows "Breaking News − N +" — with a default of 4, superseding §3.1's fixed ~4-slot preempt.)
2. **Topic budgets, in sequence.** Fill each topic category to its **own** `allocation_slot_count`, in `allocation_sort_order`, from its top-Score qualifying (`Score ≥ T`, §1) entity-aware candidates.
3. **Source soft-roll.** `YouTube` and `X` are **source-axis** categories (phase-5d); no interest slug maps to them, so they hold zero items today. Their budgeted slots (plus any topic/breaking shortfall) **roll into the topic categories by sequence** until the feed reaches `total_target` — so the feed still totals 30 with no allocator change when source ingestion lands. Roll-over walks the sequence, so the **first** topic categories absorb the surplus.
4. **Order + dedup.** Breaking first, then topic categories in the user's sequence; 1-based contiguous `feed_position`; **§3.8 don't-repeat** (exclude prior-`daily_feeds` stories) and within-feed dedup preserved verbatim; each row's `feed_slot_kind ∈ {breaking, interest}` (the `exploration` kind is retired — see below).

**Default allocation (pre-screen users).** A user with **no** `user_feed_allocation` rows gets the balanced fallback: `breaking 4` + an even split of the remaining 26 across the topic categories that have available stories (largest-remainder; empty categories not budgeted). This replaces §3's affinity-proportional default so pre-screen users still get a feed.

### 3a.3 Conflicts surfaced (Rule 7)

- **Supersedes §3 steps 2–6** (proportional split / floor-1 / ~40% cap / redistribute): the user now sets the split **explicitly** via per-category budgets. §3 remains documented as the M1 history; phase-5a is the active model for signed-in users with an allocation.
- **Retires §3.7 (auto-exploration ~10%):** there is **no** exploration reserve and **no** `exploration` slot kind is ever emitted. The user controls breadth via their category budgets (e.g. budgeting a category they don't yet follow), so an algorithm-chosen exploration tier is redundant under the user-set model. This is intentional, not a silent drop.

---

## 4. Profile-update loop (the engine that makes it feel personal over time)

A **daily job runs FIRST** (before scoring), aggregating `player_signals` since the last run and nudging `user_interest_profile.profile_weight` on the matched interest (and its ancestors, attenuated):

| Signal (`player_signal_event`) | Weight effect |
|--------------------------------|---------------|
| `complete`, `save`, `ask`, `voice` | strong **+** |
| `play` (partial) | small **+** scaled by `completion_pct` |
| `skip` (fast, low `dwell_ms`) | **−** |
| `open_detail` | mild **+** |

**Follow boost (M3, phase-3d).** A `follow` does **not** nudge as a one-shot
`player_signals.follow` event. Instead it persists in the `follows` table and the
daily job re-applies a strong **+** (`FOLLOW_BOOST_DELTA`) to the followed story's
matched interest node(s) **on every run while the story stays followed** — the
persistent `follows` set is the source of truth (a transient `player_signals.follow`
row is inert, so a follow is counted once). The boost is the **same bounded
contribution** as a signal: it shares the per-run cap, the slow decay, and the
floor/ceiling clamp below — it cannot push a weight past the ceiling or collapse
the feed (it stays inside the §4 bounds + §3 invariants). Un-following stops the
boost; the weight then decays back toward baseline.

**Guards against over-narrowing (the brief's explicit caution):**
- nudges are **bounded** (a per-run max delta and absolute floor/ceiling on `profile_weight`),
- weights **decay slowly** toward baseline each run (an ignored interest fades, it doesn't snap to zero),
- the §3 **floor-1 + ~40% cap + ~10% exploration** invariants stop the feed collapsing onto one topic even if weights drift.

New nudged rows use `profile_source = 'signal'`; explicit onboarding picks stay `'typed'` (or `'voice'` in M3).

---

## 5. Where it materializes — `daily_feeds`

The batch writes one `daily_feeds` row per (user, story, position) for today's `feed_date`. The client (`getDailyFeed(userId, feedDate)` in `src/lib/feed/supabaseFeed.ts`) reads its own rows under RLS (`feed_user_id = auth.uid()`), joins `stories → current digests → caption_sentences`, orders by `feed_position`, and returns the **unchanged `Story[]` contract** (`src/types/feed.ts`) — the reel components never change.

---

## 6. Relationship to the master-plan "world vs my field" question

The master plan (Open Q2/Q3) framed feed composition as "guaranteed world tier + personalized tier." This spec **resolves it toward personalization-first**: there is no always-on world tier. Instead:
- The **breaking tier** (§3.1) surfaces genuinely big stories — but only ones tagged to nodes the user follows (or via exploration), not a global world feed.
- A user who wants broad coverage simply follows broad depth-0 categories (`world`, `business`); a `strict` cricket-only user gets cricket only.
This honors the owner's "if they want only cricket, give only cricket" directive while breaking news still preempts within their interests.
