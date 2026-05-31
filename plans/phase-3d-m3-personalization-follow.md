# Phase 3d: Personalization + follow + what's-new

**Milestone:** M3 — Voice mode + follow
**Status:** Not started
**Estimated effort:** S–M (reduced — see re-scope)

> ⚠ **Re-scoped 2026-05-30 — shrunk by the M1 personalization pull-forward.** SP1 (engagement-signal instrumentation + signal→weight job) and SP2 (interest-weighted feed ranking) now ship in **`phase-1d-daily-content-pipeline.md`** (the daily profile-update job + the per-user scorer/allocator → `daily_feeds`, per `reference/ranking-spec.md`). The `player_signals` table + its `logSignal` call sites also land in M1 (schema in migration 0003; call sites with the reel/detail surfaces). The master-plan "world tier + niche tier" framing (SP2) is **superseded** by the breaking-tier-within-interests model (`ranking-spec.md` §6). This phase reduces to **SP3 (follow-a-story)** + **SP4 (Following view + "what's new since you last watched")**; the `follows` table + its RLS (not in migration 0003) ships with **this** phase's migration. When executing, treat SP1–SP2 as **done in M1**.

## Goal
The reel adapts to the user's interest profile + engagement signals (with a guaranteed world tier), and a user can follow a story and see "what's new since you last watched" — closing M3's "feed adapts, follow works" promise.

## Sub-phases

### Sub-phase 1: Engagement-signal instrumentation + signal→weight job
- **Files touched:** `src/lib/signals.ts` (extend from 3b), call sites in `src/components/reel/*` + `src/components/detail/*`, `agents/memory/session_processor.py` (ADAPT TLDW), Trigger.dev task `trigger/update-interest-weights.ts`
- **What ships:** A thin `logSignal(event, story_id, …)` helper plus surgical call sites writing `player_signals` at the real interaction points — `play`/`complete`/`skip` (reel), `open_detail`/`ask` (detail) — complementing the `voice` signal from 3b. A scheduled job (ADAPT TLDW `session_processor.py`) aggregates `player_signals` per category and **nudges `user_interest_profile.profile_weight`** (`profile_source='signal'`), so engagement reshapes the profile over time.
- **Definition of done:** A reel skip writes a `player_signals` `skip` row with `dwell_ms`; a completed story writes `complete` with `completion_pct≈1`; running the aggregation job over seeded signals raises the weight of an engaged category and lowers an ignored one (asserted on the resulting `user_interest_profile` rows — tests the prioritization logic, not just the insert).
- **Dependencies:** Phase 3 SP1. **Cross-milestone:** call sites live in M1 reel / M2 detail components.

### Sub-phase 2: Interest-weighted feed ranking (world tier + niche tier)
- **Files touched:** `supabase/migrations/<ts>_m3_feed_ranking.sql` (ranking RPC/view), `src/lib/feed.ts`, `agents/pipeline/stages/ranking.py` (ADAPT — interest weighting)
- **What ships:** The reel feed query becomes interest-aware: a Supabase RPC/SQL that orders stories by the user's `user_interest_profile` weights, **with a guaranteed "top world stories always in" tier** ahead of the personalized tier (honoring `user_interest_traits.prefers_world_first`). Resolves master-plan Open Q2/Q3 (world vs field) via two tiers, not one. `src/lib/feed.ts` calls it; falls back to recency for un-onboarded users.
- **Definition of done:** For a user weighted toward `markets`, the ranked feed places markets stories above unweighted ones **while still including the guaranteed world-tier stories** (asserted on RPC output against seeded stories+profile); a user with no profile gets a sensible recency-ordered feed (no crash, no empty feed).
- **Dependencies:** Sub-phase 1; Phase 3c (a profile exists to rank by). ⚠ adds a migration (RPC) — additive, but a public data-shape.

### Sub-phase 3: Follow-a-story
- **Files touched:** `src/components/reel/ActionRow.tsx` (wire follow btn), `src/lib/follows.ts`
- **What ships:** The reel/detail follow action (prototype `act-btn follow` → `follow-on` accent state) wired to `follows` inserts/deletes scoped to `auth.uid()`; viewing a followed story advances `follow_last_seen_at` (the cutoff for the NEW badge).
- **Definition of done:** Tapping follow inserts a `follows` row and flips the button to the `follow-on` accent; tapping again removes it; opening a followed story updates `follow_last_seen_at` to ~now (asserted against mocked Supabase).
- **Dependencies:** Phase 3 SP1. **Cross-milestone:** `ActionRow` chrome is built in M1.

### Sub-phase 4: Following view + "what's new since you last watched"
- **Files touched:** `src/components/following/FollowingView.tsx`, `src/components/following/FollowedStoryCard.tsx`, `src/components/reel/AllCaughtUp.tsx` (while-you-were-out card), `src/components/profile/ProfileSheet.tsx` (minimal entry)
- **What ships:** `FollowingView` listing followed stories with `● NEW` vs `NO CHANGE` markers computed by the §3 "what's new" query (latest `story_timeline` event newer than `follow_last_seen_at`); the `AllCaughtUp` "while you were out" card surfacing followed-story updates at the 30/30 finish; a minimal profile-sheet entry point (the only navigation into Following — no full profile/saves).
- **Definition of done:** A followed story with a timeline event after `follow_last_seen_at` shows `● NEW`; one without shows `NO CHANGE`; an empty follow set shows the "nothing followed yet" state; the all-caught-up card lists exactly the followed stories with updates (asserted against the query over seeded `follows`+`story_timeline`).
- **Dependencies:** Sub-phase 3. **Cross-milestone:** depends on `story_timeline` (M2) and `AllCaughtUp` (M1).

## Phase-level definition of done
A user's reel is ordered by their interest profile + engagement signals with a guaranteed world tier (un-onboarded users still get a feed); following a story persists and surfaces correctly; the Following view and the all-caught-up card both show accurate "what's new since you last watched" badges from real timeline data.

## Out of scope
- `saves` table / saved-list and the full profile sheet (only the Following entry ships here).
- `play_sessions` / streak / the 3×/week metric instrumentation (M4).
- Multi-signal behavioral ML — v1 is category prioritization only (Decision #8).

## Open questions
- **Ranking host:** ranking runs as a Supabase RPC (static-export constraint → Supabase-direct reads). Confirm vs. a worker-precomputed ranked feed if the RPC gets heavy.
- **Signal→weight cadence:** how often the weight-nudge job runs (daily?) and how aggressively signals move weights (avoid over-narrowing the feed — brief Q3 caution).
- **M1/M2 dependency:** call sites (SP1), `ActionRow` (SP3), `story_timeline`/`AllCaughtUp` (SP4) all live in M1/M2 surfaces — confirm they exist before execution.

## Self-critique

**Product lens:** PASS — delivers the brief's simple personalization + follow/what's-new; the two-tier ranking (guaranteed world + personalized) directly resolves the brief's "world vs my field" contradiction (Open Q3) and guards against over-narrowing (Q3 caution). Stays at category prioritization (Decision #8) — no ML creep. saves/streak correctly deferred.
**Engineering lens:** PASS — SP1 (signal→weight compute, ADAPT `session_processor.py`) and SP2 (ranking query, ADAPT `ranking.py`) are genuinely distinct TLDW modules, not one thing split for symmetry. DoDs assert on ranking/weight *outputs* (business logic), not inserts alone (Rule 9). Un-onboarded fallback prevents an empty feed.
**Risk lens:** SP2 adds a ranking RPC migration (additive). Cross-milestone touches into M1/M2 components in SP1/SP3/SP4 — flagged; surgical (one-line `logSignal` calls, one follow handler) per Rule 3. `src/lib/signals.ts` is created in 3b and extended here — ordering dependency (3b before 3d), not a conflict. "Painting into a corner" check: SP4 (Following/what's-new) works given SP3's `follow_last_seen_at` writes — verified by walking SP1→2→3→4.
**Irreversible sub-phases:** Sub-phase 2 adds a ranking RPC (additive, reversible by drop) — not data-destructive; noted for care, not blocked.
