# Phase 3d: Follow-as-ranking-signal

**Milestone:** M3 — Voice mode + follow
**Status:** Not started
**Estimated effort:** S (reduced again — see 2026-05-31 simplification)

> ⚠ **Re-scoped 2026-05-30 — shrunk by the M1 personalization pull-forward.** SP1 (engagement-signal instrumentation + signal→weight job) and the *base* of SP2 (interest-weighted feed ranking) ship in **`phase-1d-daily-content-pipeline.md`** (the daily profile-update loop + per-user scorer/allocator → `daily_feeds`, per `reference/ranking-spec.md`). The `player_signals` table + `follow` enum value land in M1 (migration `0003`). When executing, treat SP1 and the SP2 base as **done in M1**.
>
> ⚠ **Simplified 2026-05-31 — follow is now a ranking signal, not a reading surface.** The old SP4 (Following view + "what's new since you last watched" + the all-caught-up follow-update card) is **removed**. Following a story no longer opens a second place to check updates; instead it **persists in a minimal `follows` table** that the daily profile-update job reads as a bounded **boost to that story's matched interest node(s)** — so the followed subniche gets higher weight in *tomorrow's* feed. Net active work: **SP3 (follow toggle + `follows` table)** + the **SP2 follow-boost extension** to the existing M1 daily job. No NEW badge, no `follow_last_seen_at`, no separate update reels.

## Goal
A user can follow a story; the follow **persists** as an un-followable toggle (the prototype `follow-on` accent), and acts as a **strong, bounded boost** to that story's matched interest node(s) in the next daily profile-update run — so "follow" simply tells the ranker *"more of this subniche."* No dedicated Following surface; the personalization shows up as a shifted reel feed.

## Sub-phases

### Sub-phase 1: Engagement-signal instrumentation + signal→weight job — DONE-IN-M1
Shipped in `phase-1d` SP4 (`agents/memory/session_processor.py` + `player_signals.py`) and `0003` (`player_signals` table, `player_signal_event` enum incl. `follow`). Not executed here.

### Sub-phase 2: Interest-weighted ranking + **follow boost** (MODIFIED)
- **Base — DONE-IN-M1:** the per-user `Score` + fallback tree + ~30-slot allocator (`ranking-spec.md` §1–3) ship in `phase-1d` (`agents/pipeline/stages/ranking.py`, `feed_assembly.py`). **This sub-phase adds ONLY the follow contribution** — no change to the base scorer/allocator.
- **Files touched:** `agents/memory/session_processor.py` (ADAPT — after aggregating `player_signals`, also read each user's **`follows` set**, join via `story_interests` to the followed story's matched node(s), and apply a **bounded, slow-decay strong `+`** to `user_interest_profile.profile_weight` on those nodes — same write path as the signal nudges, `profile_source='signal'`), `reference/ranking-spec.md` §4 (update: follow's weight contribution is sourced from the **persistent `follows` set**, not a one-shot `player_signals.follow` row — resolves the stale §4 listing). ⚠ §4 edit touches the M1 contract — apply at 3d execution, not before.
- **What ships:** a currently-followed story persistently boosts its matched interest weight on each daily run, inside the **existing over-narrowing guards** (bounded per-run delta, slow decay toward baseline, and the §3 floor-1 / ~40%-cap / ~10%-exploration invariants) — so tomorrow's feed weights that subniche higher *without collapsing onto it*.
- **Definition of done:** over seeded `follows` + `story_interests`, running the profile-update job **raises** the matched interest's `profile_weight` for a following user vs. an identical non-following user (asserted on resulting `user_interest_profile` rows — the prioritization logic, not the insert, Rule 9); un-following before the run yields **no** boost; the boost stays **within bounds/decay** (no over-narrowing past the floor).
- **Dependencies:** SP3 (the `follows` table must exist); the M1 daily job (`session_processor.py`) already exists.

### Sub-phase 3: Follow toggle + `follows` table as a persistent ranking signal (MODIFIED)
- **Files touched:** `supabase/migrations/000N_m3_follows.sql` (NEW, next sequential — `follows`: `follow_user_id`, `follow_story_id`, `follow_created_at`, unique `(follow_user_id, follow_story_id)`, RLS owner-all on `auth.uid()`), `src/lib/follows.ts` (NEW — `toggleFollow`/`isFollowing` scoped to `auth.uid()`), `src/components/reel/ActionRow.tsx` (wire the follow btn → toggle + `follow-on` accent state).
- **What ships:** the prototype follow action wired to `follows` insert/delete (an **un-followable toggle**); the button flips to the `follow-on` accent and reflects persisted state on reload. The persisted set is consumed by SP2's daily boost. **NO** `follow_last_seen_at`, **NO** NEW badge, **NO** Following view.
- **Definition of done:** tapping follow inserts a `follows` row and flips to `follow-on`; tapping again deletes it and reverts the accent (asserted against mocked Supabase); `isFollowing(story_id)` reflects the persisted row.
- **Dependencies:** Phase 3 SP1 (user schema + RLS exist via `0003`). **Cross-milestone:** `ActionRow` chrome is built in M1.

### Sub-phase 4: Following view + "what's new since you last watched" — **REMOVED (2026-05-31)**
Cut. `FollowingView.tsx`, `FollowedStoryCard.tsx`, the `AllCaughtUp` "while you were out" follow-update card, the `ProfileSheet` Following entry, `follow_last_seen_at`, and the `story_timeline`-based "what's new" query are **not built**. Rationale: follow is now a ranking signal, not a second reading surface — the payoff is "more of this in tomorrow's feed," not another place to check updates. **Consequence:** `AllCaughtUp` (M1) remains a plain end-of-feed card with no follow content.

## Phase-level definition of done
Following a story persists as an un-followable toggle and, on the next daily profile-update run, boosts that story's matched interest node(s) weight **within the over-narrowing guards** — so the user's reel feed shifts toward the followed subniche, with **no separate Following surface**.

## Out of scope
- **Removed 2026-05-31:** Following view, "what's new since you last watched", the all-caught-up follow-update card, `follow_last_seen_at`, the `story_timeline` "what's new" query.
- `saves` table / saved-list and the full profile sheet.
- `play_sessions` / streak / the 3×/week metric instrumentation (M4).
- Multi-signal behavioral ML — v1 is category prioritization only (Decision #8).

## Open questions
- **Follow boost strength:** how much stronger than a `complete`/`save` nudge should a *persistent* follow be, and should the boost itself decay if the user stops engaging with that story's topic? Tune in SP2 within the §4 bounds (avoid over-narrowing — brief Q3 caution).
- **Ranking-spec §4 edit** touches the M1 contract — apply at 3d execution (not now) to avoid drift while phase-1d code settles. Flagged (Rule 7/12).
- **Cross-doc staleness:** docs referencing "3d follow/what's-new" (master-plan, memory) now overstate scope — clean up opportunistically.

## Self-critique

**Product lens:** PASS — simpler and on-brief. Follow = "more of this subniche tomorrow," matching the owner's 2026-05-31 call; drops a second surface (Following / what's-new) that competed with the single reel. Stays at category prioritization (Decision #8) — no ML creep.
**Engineering lens:** PASS — collapses to **two active sub-phases** (follow table + toggle; daily boost) reusing the existing M1 `session_processor.py` join. The `follows`-set boost uses the **same `story_interests` path** as the signal nudges — no new mechanism, one write target (`profile_weight`). DoDs assert on weight **outputs** (Rule 9), not inserts.
**Risk lens:** SP3 adds a `follows` migration (additive, reversible by drop). SP2 edits the M1 `ranking-spec.md` §4 contract + `session_processor.py` — flagged; surgical and bounded by the existing over-narrowing guards. Removing SP4 strands no dependency (`AllCaughtUp` stands alone as an end card). **Parallel-safe** vs. the in-flight phase-3 voice worktrees (`../News20-sub-3` Gemini Live, `../News20-sub-4` voice UI) — disjoint file sets.
**Irreversible sub-phases:** SP3's `follows` migration is additive/reversible (drop) — not data-destructive; noted for care, not blocked.
