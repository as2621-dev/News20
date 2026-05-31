# Ranking spec — per-user personalized feed (M1)

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

## 4. Profile-update loop (the engine that makes it feel personal over time)

A **daily job runs FIRST** (before scoring), aggregating `player_signals` since the last run and nudging `user_interest_profile.profile_weight` on the matched interest (and its ancestors, attenuated):

| Signal (`player_signal_event`) | Weight effect |
|--------------------------------|---------------|
| `complete`, `save`, `follow`, `ask` | strong **+** |
| `play` (partial) | small **+** scaled by `completion_pct` |
| `skip` (fast, low `dwell_ms`) | **−** |
| `open_detail` | mild **+** |

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
