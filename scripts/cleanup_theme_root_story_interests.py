"""One-time cleanup of old-shape theme-root ``story_interests`` rows (issue #72).

What went wrong
---------------
Between a953ef3 (2026-06-30) and c38092f (2026-07-25T18:54:43Z) the SP3 tag writer
emitted the story's THEME-derived category as a depth-0 **ROOT** ``story_interests``
row and shifted every real keyword tag to ``min(depth + 1, 2)``. #70 fixed the
writer — the theme now rides beside the tags as an explicit side-channel and keyword
tags keep their natural depth (leaf 0 / parent 1 / grandparent 2). It did NOT touch
the rows already persisted, and those rows are still consumed LIVE:

  * ``agents/worker/pipeline_routes.py::_load_ready_story_pool`` (behind
    ``POST /feed/assemble-for-user`` and ``/feed/assemble-mine``) has no date scoping
    and no theme map, so a phantom depth-0 root still wins ``assign_category`` —
    the scrambled chips (cricket → tech, Netflix → geopolitics) remain live.
  * ``agents/memory/session_processor.py`` hands the full ``DEPTH_ATTENUATION`` 1.0x
    engagement nudge to a root the story never matched, and only 0.5x to the real
    leaf, corrupting ``user_interest_profile`` weights on every such engagement.
  * ``story_interests`` persistence is INSERT-only, so a story re-produced after the
    cutover keeps its stale depth-0 root ALONGSIDE the new depth-0 leaf — an
    equal-depth cross-root tie, the scrambled shape resurrected.

The discriminator (and why an inverse depth shift is NOT attempted)
-------------------------------------------------------------------
Inside the window the old writer shifted EVERY keyword tag to depth >= 1, so a row
with ``story_interest_match_depth = 0`` whose interest is a ROOT node
(``interests.depth_level = 0``) can only be a theme tag. Those rows are DELETED.

Keyword rows are left exactly as they are: the ``min(depth + 1, 2)`` clamp collapsed
parent and grandparent into a single persisted ``2``, so un-shifting them would be a
guess. Post-delete depths stay slightly attenuated, which is the safe direction — an
under-scored real match costs ranking precision; an invented depth corrupts it.

The date scope is the load-bearing guardrail
--------------------------------------------
A depth-0 row on a ROOT interest is ALSO the correct post-#70 shape for a story that
genuinely matches a followed root. Shape alone cannot tell the two apart — only the
timestamp can. Hence the window is closed at BOTH ends and the DELETE never reaches
past :data:`THEME_TAG_CUTOVER_UTC`, which also makes it safe to run while another
pipeline run is writing.

SAFETY
------
  * **Dry-run by default** — counts, the ``created_at`` distribution and the
    post-cutover race check are printed; nothing is written.
  * ``--apply`` additionally requires typing ``APPLY`` (or ``--yes``).
  * A JSON **snapshot** of every row about to be deleted is written BEFORE the first
    delete (``--snapshot-path``, default ``.agents/backups/``). The delete aborts if
    the snapshot cannot be written — no snapshot, no mutation.
  * The DELETE targets explicit ``story_interest_id`` values from the snapshot, so it
    cannot widen if the table changes between the scan and the write.
  * **Idempotent** — a second run finds 0 rows in the window and reports clean.
  * No table references ``story_interests.story_interest_id``, so the delete cannot
    cascade to other rows.

ROLLBACK
--------
The snapshot carries every column of every deleted row, including the original
``story_interest_id`` and ``story_interest_created_at`` (plain defaulted columns, not
generated), so a re-insert restores the exact rows. Use ``ON CONFLICT DO NOTHING`` —
a run that failed part-way leaves some rows still present, and ``uq_story_interest``
would otherwise abort the restore.

Run (dry-run, read-only) — ALWAYS first:
    .venv/bin/python scripts/cleanup_theme_root_story_interests.py

Run (writes to prod):
    .venv/bin/python scripts/cleanup_theme_root_story_interests.py --apply

Env (from .env, never logged): ``SUPABASE_URL``, ``SUPABASE_SERVICE_ROLE_KEY``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.shared.logger import get_logger  # noqa: E402

logger = get_logger("scripts.cleanup_theme_root_story_interests")

# Reason: a953ef3 (2026-06-30) introduced the theme-root tag writer. Rows created
# before it come from the natural-depth era, where a depth-0 root row is a genuine
# root match — out of scope, inclusive lower bound.
OLD_WRITER_WINDOW_START_UTC = datetime(2026, 6, 30, tzinfo=timezone.utc)
# Reason: c38092f — "fix(pipeline): stop theme tags scrambling shortlist chips +
# matched slugs", committed 2026-07-25 13:54:43 -0500. Rows at or before this instant
# came from the old writer; anything after is the new shape (or a concurrent run) and
# is NEVER touched. Inclusive upper bound: the last old-writer run could have written
# up to this second.
THEME_TAG_CUTOVER_UTC = datetime(2026, 7, 25, 18, 54, 43, tzinfo=timezone.utc)

_TAG_COLS = (
    "story_interest_id,story_interest_story_id,story_interest_interest_id,"
    "story_interest_match_depth,story_interest_relevance,story_interest_created_at"
)
# Reason: PostgREST caps a single .select() at 1000 rows and silently truncates past
# it; story_interests grows without bound. Page explicitly (same rule as
# agents/pipeline/coverage_census.py::_fetch_all).
_PAGE_SIZE = 1000
# Reason: a large id list overflows the request URL — the chunk size the daily batch
# already uses for .in_() (agents/pipeline/daily_batch.py::_load_has_current_digest).
_ID_CHUNK_SIZE = 150


def parse_created_at(raw_created_at: Any) -> datetime | None:
    """Parse a ``story_interest_created_at`` value into an aware UTC datetime.

    Args:
        raw_created_at: The raw column value as supabase-py returns it (ISO-8601
            string, possibly ``Z``-suffixed), or anything unparseable.

    Returns:
        The timestamp as an aware UTC datetime, or ``None`` when it cannot be parsed.
        ``None`` means "cannot be proven in-window", which the discriminator treats
        as KEEP.

    Example:
        >>> parse_created_at("2026-07-07T09:00:00Z").month
        7
        >>> parse_created_at("nope") is None
        True
    """
    if not isinstance(raw_created_at, str) or not raw_created_at:
        return None
    try:
        parsed = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def select_theme_root_tag_rows(
    tag_rows: list[dict[str, Any]],
    root_interest_ids: set[str],
    window_start_utc: datetime = OLD_WRITER_WINDOW_START_UTC,
    cutover_utc: datetime = THEME_TAG_CUTOVER_UTC,
) -> list[dict[str, Any]]:
    """Pick the ``story_interests`` rows that can only be old-shape theme tags.

    A row qualifies when ALL FOUR hold:

      1. ``story_interest_match_depth == 0`` — the old writer put keyword tags at
         depth >= 1, so depth 0 in-window is not a keyword match.
      2. its interest is a ROOT node (``interests.depth_level = 0``) — a depth-0 row
         on a non-root interest is the correct post-#70 leaf shape.
      3. ``created_at >= window_start_utc`` — before the old writer existed, a
         depth-0 root row is a genuine root match.
      4. ``created_at <= cutover_utc`` — after #70, a depth-0 root row is a genuine
         root match again (identical in shape), so the cutover is the ONLY thing
         separating the two. This bound is what makes the run safe to execute while
         another pipeline run is writing.

    Anything whose timestamp cannot be parsed is KEPT: a surviving phantom tag is the
    already-live status quo, while a wrongly deleted row is only recoverable from the
    snapshot.

    Args:
        tag_rows: Raw ``story_interests`` rows carrying at least
            ``story_interest_interest_id``, ``story_interest_match_depth`` and
            ``story_interest_created_at``.
        root_interest_ids: ``interests.interest_id`` values whose ``depth_level`` is
            0. An EMPTY set selects nothing (never a wildcard) — an empty/failed
            roots lookup must not turn into "delete every depth-0 row".
        window_start_utc: Inclusive lower bound (the old writer's first commit).
        cutover_utc: Inclusive upper bound (the #70 fix commit).

    Returns:
        The subset of ``tag_rows`` to delete, in input order.

    Example:
        >>> rows = [{"story_interest_interest_id": "r1",
        ...          "story_interest_match_depth": 0,
        ...          "story_interest_created_at": "2026-07-07T09:00:00+00:00"}]
        >>> len(select_theme_root_tag_rows(rows, {"r1"}))
        1
    """
    if not root_interest_ids:
        return []
    selected: list[dict[str, Any]] = []
    for row in tag_rows:
        # Reason: an ABSENT/NULL depth must KEEP the row, never coerce to 0 — a
        # falsy-or-zero read would widen an irreversible delete on a missing field.
        raw_depth = row.get("story_interest_match_depth")
        if not isinstance(raw_depth, int) or raw_depth != 0:
            continue
        if str(row.get("story_interest_interest_id")) not in root_interest_ids:
            continue
        created_at = parse_created_at(row.get("story_interest_created_at"))
        if created_at is None:
            continue
        if window_start_utc <= created_at <= cutover_utc:
            selected.append(row)
    return selected


def _fetch_root_interest_ids(supabase_client: Any) -> set[str]:
    """Load every ROOT (``depth_level = 0``) interest id.

    Unpaged on purpose: roots are the 8 picker categories plus a handful of legacy
    aliases (15 in prod), far under the PostgREST 1000-row cap. Were it ever to
    truncate, the effect is a SMALLER root set — i.e. fewer rows selected for
    deletion, the safe direction.

    Raises:
        RuntimeError: When the lookup comes back empty. Fail LOUD (Rule 12) — an
            empty roots set would make the discriminator a silent no-op, and a
            "0 rows to delete" report would be indistinguishable from a clean table.
    """
    rows = (
        getattr(
            supabase_client.table("interests")
            .select("interest_id,interest_slug")
            .eq("depth_level", 0)
            .execute(),
            "data",
            None,
        )
        or []
    )
    if not rows:
        raise RuntimeError(
            "no depth_level=0 interests found. "
            "fix_suggestion: check SUPABASE_URL points at prod and the service-role "
            "key can read `interests` — an empty root set makes this cleanup a no-op."
        )
    return {str(row["interest_id"]) for row in rows}


def _fetch_depth_zero_tag_rows(supabase_client: Any) -> list[dict[str, Any]]:
    """Page every ``story_interests`` row at ``match_depth = 0``.

    Scoped server-side to depth 0 (the only depth the discriminator can select) so
    the scan reads the candidate slice rather than the whole table, and ordered by
    primary key so paging is stable across requests.
    """
    all_rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            getattr(
                supabase_client.table("story_interests")
                .select(_TAG_COLS)
                .eq("story_interest_match_depth", 0)
                .order("story_interest_id")
                .range(offset, offset + _PAGE_SIZE - 1)
                .execute(),
                "data",
                None,
            )
            or []
        )
        all_rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return all_rows
        offset += _PAGE_SIZE


def summarize_created_at(tag_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Min / max / per-UTC-date counts of a row set's ``created_at``.

    Proves the affected window from the DATA rather than assuming the commit dates
    bound it — the issue's "06-30 → 07-07 producing runs" claim is a hypothesis until
    this prints.
    """
    timestamps = [
        parsed
        for parsed in (
            parse_created_at(row.get("story_interest_created_at")) for row in tag_rows
        )
        if parsed is not None
    ]
    if not timestamps:
        return {"min": None, "max": None, "by_utc_date": {}}
    return {
        "min": min(timestamps).isoformat(),
        "max": max(timestamps).isoformat(),
        "by_utc_date": dict(
            sorted(Counter(ts.date().isoformat() for ts in timestamps).items())
        ),
    }


def _write_snapshot(snapshot_path: str, rows: list[dict[str, Any]]) -> None:
    """Write the exact delete manifest to disk; raise if it cannot be persisted."""
    os.makedirs(os.path.dirname(snapshot_path) or ".", exist_ok=True)
    payload = {
        "issue": 72,
        "window_start_utc": OLD_WRITER_WINDOW_START_UTC.isoformat(),
        "cutover_utc": THEME_TAG_CUTOVER_UTC.isoformat(),
        "row_count": len(rows),
        "rows": rows,
    }
    with open(snapshot_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _delete_tag_rows(supabase_client: Any, tag_row_ids: list[str]) -> int:
    """Delete the named ``story_interests`` rows by primary key; return rows removed.

    Targets explicit ``story_interest_id`` values (never a predicate re-evaluated
    server-side) so the write cannot widen beyond the snapshotted manifest.
    """
    deleted = 0
    for start in range(0, len(tag_row_ids), _ID_CHUNK_SIZE):
        chunk = tag_row_ids[start : start + _ID_CHUNK_SIZE]
        response = (
            supabase_client.table("story_interests")
            .delete()
            .in_("story_interest_id", chunk)
            .execute()
        )
        deleted += len(getattr(response, "data", None) or [])
    return deleted


def _confirm_apply(delete_count: int, assume_yes: bool) -> bool:
    """Loud interactive gate in front of the irreversible write path."""
    if assume_yes:
        return True
    print(
        f"\nAbout to DELETE {delete_count} story_interests row(s) from production.\n"
        "Type APPLY to continue, anything else to abort: ",
        end="",
    )
    return input().strip() == "APPLY"


def main() -> int:
    """Scan, report, snapshot and (with ``--apply``) delete the old-shape theme tags."""
    parser = argparse.ArgumentParser(description="Issue #72 story_interests cleanup.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to production. Without this the run is read-only.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation (only meaningful with --apply).",
    )
    parser.add_argument(
        "--snapshot-path",
        default=os.path.join(
            _REPO_ROOT,
            ".agents",
            "backups",
            "story_interests-cleanup-72-2026-07-25.json",
        ),
        help="Where the pre-delete row snapshot is written (the rollback manifest).",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase_client = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    root_interest_ids = _fetch_root_interest_ids(supabase_client)
    depth_zero_rows = _fetch_depth_zero_tag_rows(supabase_client)
    to_delete = select_theme_root_tag_rows(depth_zero_rows, root_interest_ids)

    # Reason: the race check the slice must answer explicitly — a concurrent
    # SHORTLIST_ONLY audit run (#47) is asserted to write NOTHING to story_interests.
    # Verify that from the data instead of trusting the assertion.
    on_or_after_cutover = [
        row
        for row in depth_zero_rows
        if (parsed := parse_created_at(row.get("story_interest_created_at")))
        is not None
        and parsed > THEME_TAG_CUTOVER_UTC
    ]
    root_rows_after_cutover = [
        row
        for row in on_or_after_cutover
        if str(row.get("story_interest_interest_id")) in root_interest_ids
    ]

    distinct_story_count = len(
        {str(row["story_interest_story_id"]) for row in to_delete}
    )
    distribution = summarize_created_at(to_delete)

    print(f"root interests (depth_level=0):        {len(root_interest_ids)}")
    print(f"story_interests rows at match_depth=0: {len(depth_zero_rows)}")
    print(f"MATCHED old-shape theme-root rows:     {len(to_delete)}")
    print(f"  distinct stories affected:           {distinct_story_count}")
    print(f"  created_at min:                      {distribution['min']}")
    print(f"  created_at max:                      {distribution['max']}")
    for utc_date, count in distribution["by_utc_date"].items():
        print(f"    {utc_date}  {count}")
    print(
        f"depth-0 rows created AFTER the cutover: {len(on_or_after_cutover)} "
        f"(of which on a ROOT interest: {len(root_rows_after_cutover)}) — NEVER touched"
    )
    print(
        f"window: {OLD_WRITER_WINDOW_START_UTC.isoformat()} .. {THEME_TAG_CUTOVER_UTC.isoformat()}"
    )

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to delete these.")
        return 0
    if not to_delete:
        print("\nNothing to delete — already clean.")
        return 0
    if not _confirm_apply(len(to_delete), args.yes):
        print("\nAborted — nothing written.")
        return 1

    # Reason: snapshot BEFORE the first delete and let a write failure abort the run —
    # this file is the only rollback for an irreversible DELETE.
    _write_snapshot(args.snapshot_path, to_delete)
    print(f"\nsnapshot written: {args.snapshot_path}")

    tag_row_ids = [str(row["story_interest_id"]) for row in to_delete]
    deleted_count = _delete_tag_rows(supabase_client, tag_row_ids)
    logger.info(
        "theme_root_story_interests_deleted",
        deleted_count=deleted_count,
        expected_count=len(tag_row_ids),
        distinct_story_count=distinct_story_count,
        snapshot_path=args.snapshot_path,
    )

    remaining = select_theme_root_tag_rows(
        _fetch_depth_zero_tag_rows(supabase_client), root_interest_ids
    )
    print(f"deleted: {deleted_count} / {len(tag_row_ids)}")
    print(f"post-delete rows still matching the predicate: {len(remaining)}")
    if deleted_count != len(tag_row_ids) or remaining:
        # Reason: fail LOUD (Rule 12) — a short delete or a non-empty re-scan means the
        # cleanup is PARTIAL. Reporting success here would leave prod half-migrated.
        print(
            f"\nWARNING: expected to delete {len(tag_row_ids)} row(s), removed "
            f"{deleted_count}, and {len(remaining)} still match. Re-run the dry run "
            "before assuming the cleanup landed."
        )
        return 1
    print("\nCleanup complete — no old-shape theme-root rows remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
