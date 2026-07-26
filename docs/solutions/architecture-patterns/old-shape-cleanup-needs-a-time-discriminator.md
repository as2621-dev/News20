---
title: Cleaning up rows a fixed writer left behind needs a TIME discriminator — the old shape and the new correct shape are often identical
tags: [migration, backfill, data-cleanup, story_interests, irreversible-delete, cutover, blast-radius]
problem_type: pattern
symptoms: after fixing a writer, a cleanup targeting "the old shape" would also delete
  legitimate new rows, because the predicate that identifies the bug is satisfied by
  correct post-fix rows too; or the cleanup races a concurrent run that is still writing
root_cause: a writer fix changes what a shape MEANS, not what it LOOKS like — the same
  (column, value) tuple is a bug before the cutover and correct after it, so shape alone
  cannot classify a row and only created_at can
date: 2026-07-25
---

Established in #72 (cleaning up the theme-root `story_interests` rows that the pre-#70
writer emitted). Reuse for any "fix the writer, now repair the rows it already wrote"
slice. See [[side-band-signal-smuggled-into-shared-channel]] for the bug class that
creates this situation, and [[story-interests-holds-produced-not-ingested]] for what
that table actually contains.

## The trap: the bug's shape is also the fix's shape

The old writer emitted the theme-derived category as a **depth-0 row on a ROOT
interest**. So `match_depth = 0 AND interests.depth_level = 0` looks like a perfect
discriminator — until you notice that post-fix, a story genuinely matching a followed
ROOT interest is tagged **depth-0 on a root**. Byte-identical. A shape-only DELETE
would have destroyed real matches and, worse, would have kept destroying them on every
future re-run.

**The rule: scope the mutation to `created_at BETWEEN <old-writer-first-commit> AND
<fix-commit-instant>`, closed at both ends.** Take the upper bound from the fix
commit's own timestamp (`git show <sha>` → convert to UTC; c38092f's
`2026-07-25 13:54:43 -0500` = `2026-07-25T18:54:43Z`), not from "today" and not from a
round date. Pin both bounds as named constants and **assert them in a test** — a
comment cannot stop them drifting, and drift silently changes what a re-run deletes.

The date bound pays a second dividend: it makes the cleanup **safe to run while another
pipeline run is writing**, because the predicate provably cannot reach anything newer
than the cutover. That turned a "wait for the concurrent run to finish" blocker into a
non-issue.

## Count from the data before believing the ticket's blast radius

The #72 issue estimated the affected window as "≈ the 06-30 → 07-07 producing runs".
The actual span was **07-03 → 07-19** — a producing run on 07-19 had written 60 of the
212 rows, and nothing existed for 06-30 → 07-02. The delete scope was wide enough, so
nothing broke, but a narrower hand-written window would have stranded 28% of the rows.

Always emit the `created_at` **min/max plus a per-UTC-date histogram** of the matched
set in the dry run, and post it before mutating. Same discipline as
[[proving-run-mode-needs-positive-evidence-not-absent-errors]]: a ticket's blast-radius
estimate is a hypothesis, and "the concurrent run wrote nothing" is a claim to verify
with a count (it was `0` rows at any depth on/after 07-25), not an assumption to
inherit.

## Prove the premise: nothing should be left with ZERO rows

The discriminator's unstated premise was that the phantom tags were **additive** —
they sat alongside the real (shifted) keyword tags rather than replacing them. If that
were wrong, deleting them would strand stories with no tags at all, silently dropping
them to the loud `DEFAULT_CATEGORY` fallback.

So the post-delete integrity check is not "did the count go down by N" but **"how many
affected parents now have zero children?"** (0 of 146 here). That single query is what
turns "the predicate looked right" into "the premise held". Run it as a matter of
course for any child-row deletion. Also confirm no FK references the deleted table's PK
so the delete cannot cascade — `grep "references <table>" supabase/migrations/*.sql`.

## Don't reverse a lossy transform

The old writer also shifted keyword depths by `min(depth + 1, 2)`. That clamp collapsed
parent and grandparent into an indistinguishable persisted `2`, so un-shifting is a
**guess**, not a repair. Delete the unambiguous rows, leave the ambiguous ones
attenuated, and say so out loud: an under-scored real match costs some ranking
precision; an invented depth corrupts the signal permanently. Pick the direction whose
failure mode is recoverable.

## Script shape that made this safe

Mirrors `scripts/retire_unpublishable_headlines.py`; see
`scripts/cleanup_theme_root_story_interests.py`.

- Dry-run **by default**; `--apply` plus a typed `APPLY` confirmation to write.
- The predicate is **one pure function** imported by both the script and the tests, so
  the script can never develop a private opinion of what an "old-shape" row is.
- Snapshot every row to `.agents/backups/<table>-cleanup-<issue>-<date>.json` **before**
  the first delete; let a snapshot write failure abort the run. Record the rollback
  recipe as `ON CONFLICT DO NOTHING` — a part-way-failed run leaves some rows present,
  and a naive re-insert would abort on the unique constraint.
- DELETE by **explicit primary-key manifest** from the snapshot, never by re-evaluating
  the predicate server-side — the write then cannot widen if the table changes between
  the scan and the confirmation.
- An empty lookup that feeds the predicate (here: the ROOT interest ids) must **raise**,
  never return empty. An empty set silently degrades a filter into a wildcard or a
  no-op, and "0 rows to delete" then reads identically to "already clean".
- Never coerce a missing field into the delete set. `int(row.get(col) or 0) == 0`
  quietly treats NULL/absent as a match — on an irreversible path, require the value to
  actually be present and equal.
- Re-scan after the delete and return non-zero if anything still matches (Rule 12).
