# Progress: phase-3d-m3-personalization-follow

**Phase file:** plans/phase-3d-m3-personalization-follow.md
**Started:** 2026-05-31
**Phase-diff baseline commit:** 0bb7f4f (plan re-scope committed; phase code builds on this)
**Execution mode:** SEQUENTIAL — SP3 → SP2 (SP2's `session_processor.py` reads the `follows` table SP3 creates; hard dependency, no parallelism within 3d).

## Scope (verified against repo 2026-05-31)
- **SP1** — DONE-IN-M1 (`0003` player_signals + enum incl. `follow`; `session_processor.py`). Not executed.
- **SP2** — ACTIVE: follow-boost extension to `agents/memory/session_processor.py` + `reference/ranking-spec.md` §4. Depends on SP3.
- **SP3** — ACTIVE: `follows` table (migration `0005`) + `src/lib/follows.ts` + persist the lifted follow toggle in `src/components/reel/Reel.tsx`.
- **SP4** — REMOVED (Following view / what's-new cut).
- **2 active sub-phases** (not 4) — documented exception per the plan's re-scope headers.

## Repo divergences found during priming (corrected guidance given to sub-agents)
- `src/components/reel/ActionRow.tsx` **does not exist** — the plan's stale name. The follow button lives in `ReelChrome.tsx` (already prop-driven via `isFollowed`/`onToggleFollow`); toggle state is lifted into `Reel.tsx` and is currently **in-memory only**. SP3 wires persistence in `Reel.tsx` + new `src/lib/follows.ts`; `ReelChrome.tsx` should need no change.
- Next migration number is **`0005`** (existing: 0001–0004). 3b adds no migration → no collision.

## Parallel-work note (external)
- **phase-3b** (in-news voice mode) is being worked on in a separate session/worktree. 3b files: `src/components/voice/*`, `src/lib/voice/*`, `src/lib/signals.ts`, `agents/worker/main.py`, `agents/chat/prompts.py`, `src/components/shell/LayerStack.tsx`, `ios/.../Info.plist`. **Disjoint from 3d** — verified. Do not touch 3b files.

## Sub-phase progress
- [x] 1: Engagement-signal instrumentation + signal→weight job — DONE-IN-M1. Not executed.
- [x] 2: Interest-weighted ranking + follow boost — DONE. Files: agents/memory/session_processor.py, agents/memory/player_signals.py, reference/ranking-spec.md, tests/agents/memory/test_session_processor.py. Validation PASS (ruff clean, pytest 233/233, +7 new). DoD PASS. Report: sub-2.md. Source-of-truth=follows set; double-count prevented (follow removed from _STRONG_POSITIVE_EVENTS, transient follow inert); FOLLOW_BOOST_DELTA=0.40 (tunable, < per-run cap 0.5).
- [x] 3: Follow toggle + follows table as persistent ranking signal — DONE. Files: supabase/migrations/0005_m3_follows.sql, src/lib/follows.ts, src/components/reel/Reel.tsx, tests/lib/follows.test.ts. Validation PASS (biome/tsc clean, vitest 7/7 new + 26/26 no regress). DoD PASS. Report: sub-3.md.
  - **SP2 contract:** follows(follow_id uuid PK, follow_user_id uuid=auth.uid()=user_interest_profile.profile_user_id, follow_story_id **text**→stories.story_id, follow_created_at, unique(user,story)). Join: follows.follow_story_id (text) = story_interests.story_interest_story_id (text) → story_interest_interest_id (uuid) = node to boost. SP2 uses service-role (bypasses RLS).
- [x] 4: Following view + what's-new — REMOVED (not built)

## Gates / flags
- SP3 adds migration `0005_m3_follows.sql` (additive, reversible by drop) — run sequentially; do not apply live until phase commit + review.
- SP2 edits the M1 `ranking-spec.md` §4 contract — flagged; surgical, bounded by existing over-narrowing guards.
