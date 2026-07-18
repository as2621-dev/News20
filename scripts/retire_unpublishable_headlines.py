"""Retire already-persisted stories whose headline is a masthead / fragment.

Slice #45 made the headline gate fail-closed at WRITE time, and slice #62 added the
matching re-check on the read path (``agents/worker/pipeline_routes.py``). Neither
touches rows written BEFORE the gate existed — the 07-07 "Language Magazine" row is
still in ``stories``, still carrying a current digest, and still counted as placeable
by ``agents/pipeline/daily_batch.py::_load_has_current_digest``. This script is the
one-time cleanup for those rows.

"Not placeable" is not a column — in this codebase a story is placeable when it has a
``digests`` row with ``digest_is_current = true`` (the produce-once gate, the ready
pool, and the source/theme placeability filters all read exactly that). So retiring a
story means flipping its CURRENT digest to ``digest_is_current = false``, the same
supersede move ``regenerate_feed_content.py`` makes. The digest row is kept, so the
change is reversible by flipping the flag back.

SAFETY:
  * **Dry-run by default** — reports counts and exits without writing.
  * ``--apply`` is the only way to write, and it additionally requires typing
    ``APPLY`` at the prompt (or passing ``--yes`` for a non-interactive run).
  * **Idempotent** — it only ever flips digests that are currently ``true``, so a
    second run reports 0 to retire.
  * Followed-source (YouTube/X) reels are EXEMPT, matching the produce gate and the
    write-time gate: their titles are creator-written, not scraped page titles.

KNOWN LIMITS (read before running with ``--apply``):
  * It does not touch ``daily_feeds``. A bad reel already assembled into a past feed
    keeps rendering — the app's story read joins the digest without filtering on
    ``digest_is_current`` — so an already-shipped masthead reel needs a feed rebuild
    (or a slot swap) on top of this.
  * ``digest_is_current`` doubles as the PRODUCE-ONCE marker
    (``agents/pipeline/produce_gate.py``), so retiring a story also tells the pipeline
    it has never been produced. If that story is re-ingested it will be scripted and
    verified again (paid) before the write-time gate drops it. That is acceptable for a
    one-time cleanup of a handful of rows; it is NOT a suppression mechanism to run on
    a schedule.

Run (dry-run, read-only):
    .venv/bin/python scripts/retire_unpublishable_headlines.py

Run (writes to prod):
    .venv/bin/python scripts/retire_unpublishable_headlines.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.shared.logger import get_logger  # noqa: E402
from agents.shared.persisted_headline_gate import (  # noqa: E402
    load_source_origin_story_ids,
    persisted_headline_rejection_reason,
)

logger = get_logger("scripts.retire_unpublishable_headlines")

_STORY_COLS = "story_id,story_headline,story_primary_outlet_name"
# Reason: supabase-py caps a single select at 1000 rows, and ``stories`` is far past
# that — page explicitly rather than silently scanning only the first page.
_PAGE_SIZE = 1000
# Reason: same chunking the daily batch uses for ``.in_()`` — a large id list
# overflows the request URL (agents/pipeline/daily_batch.py::_load_has_current_digest).
_ID_CHUNK_SIZE = 150


def classify_unpublishable_rows(
    story_rows: list[dict[str, Any]],
    source_origin_ids: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Pick out the story rows whose headline cannot be published, with the reason.

    Uses the SAME predicate and the SAME source-origin exemption as the write gate and
    the read-side pool filter — this script must never develop its own opinion of what
    a bad headline is, or a cleanup run would retire rows the pipeline would happily
    place (and vice versa).

    Args:
        story_rows: Raw ``stories`` rows carrying at least ``story_id``,
            ``story_headline`` and ``story_primary_outlet_name``.
        source_origin_ids: Story ids that are followed-source (YouTube/X) reels. Their
            titles are creator-written, not scraped, so they are never retired.

    Returns:
        ``[(story_id, rejection_reason), ...]`` for the failing rows, in input order.

    Example:
        >>> classify_unpublishable_rows(
        ...     [{"story_id": "s1", "story_headline": "Language Magazine",
        ...       "story_primary_outlet_name": "Language Magazine"}]
        ... )
        [('s1', 'title_equals_outlet')]
    """
    exempt = source_origin_ids or set()
    unpublishable: list[tuple[str, str]] = []
    for row in story_rows:
        reason = persisted_headline_rejection_reason(row, exempt)
        if reason is not None:
            unpublishable.append((str(row["story_id"]), reason))
    return unpublishable


def _fetch_all_story_rows(supabase_client: Any) -> list[dict[str, Any]]:
    """Read every ``stories`` row (id + headline + outlet), one page at a time."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            getattr(
                supabase_client.table("stories")
                .select(_STORY_COLS)
                .order("story_id")
                .range(offset, offset + _PAGE_SIZE - 1)
                .execute(),
                "data",
                None,
            )
            or []
        )
        rows.extend(page)
        # Reason: stop on an EMPTY page, not a short one — PostgREST caps a response at
        # the project's max-rows, which may be below _PAGE_SIZE, and a short-page exit
        # would then silently scan only the first page and under-report.
        if not page:
            return rows
        offset += len(page)


def _fetch_current_digests(
    supabase_client: Any, story_ids: list[str]
) -> dict[str, str]:
    """Map ``story_id -> digest_id`` for the stories that are currently placeable.

    The partial unique index on ``digests(digest_story_id) where digest_is_current``
    guarantees at most one such digest per story, so this is 1:1.
    """
    current: dict[str, str] = {}
    for start in range(0, len(story_ids), _ID_CHUNK_SIZE):
        chunk = story_ids[start : start + _ID_CHUNK_SIZE]
        rows = (
            getattr(
                supabase_client.table("digests")
                .select("digest_id,digest_story_id")
                .in_("digest_story_id", chunk)
                .eq("digest_is_current", True)
                .execute(),
                "data",
                None,
            )
            or []
        )
        for row in rows:
            current[str(row["digest_story_id"])] = str(row["digest_id"])
    return current


def _retire_digests(supabase_client: Any, digest_ids: list[str]) -> int:
    """Flip the named digests to ``digest_is_current = false``; return rows updated.

    Targets explicit ``digest_id`` values (not story ids) so the run cannot clobber a
    NEWER digest produced between the scan and the confirmation prompt, and so the
    printed id list is an exact undo manifest. Still scoped by
    ``digest_is_current = true``, which is what makes a re-run a no-op rather than a
    rewrite of already-superseded digest history.
    """
    updated = 0
    for start in range(0, len(digest_ids), _ID_CHUNK_SIZE):
        chunk = digest_ids[start : start + _ID_CHUNK_SIZE]
        response = (
            supabase_client.table("digests")
            .update({"digest_is_current": False})
            .in_("digest_id", chunk)
            .eq("digest_is_current", True)
            .execute()
        )
        updated += len(getattr(response, "data", None) or [])
    return updated


def _confirm_apply(retire_count: int, assume_yes: bool) -> bool:
    """Loud interactive gate in front of the write path."""
    if assume_yes:
        return True
    print(
        f"\nAbout to mark {retire_count} story(ies) NOT PLACEABLE in production.\n"
        "Their current digests will be flipped to digest_is_current = false.\n"
        "Type APPLY to continue, anything else to abort: ",
        end="",
    )
    return input().strip() == "APPLY"


def main() -> int:
    """Scan ``stories``, report the unpublishable ones, and optionally retire them."""
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args()

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase_client = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    story_rows = _fetch_all_story_rows(supabase_client)
    # Reason: two passes so the source-origin lookup only queries the handful of ids
    # that could be retired, rather than ``story_sources`` for the whole table. The
    # verdict still comes from ONE function, so the script's exemption cannot drift
    # from the loader's.
    candidates = classify_unpublishable_rows(story_rows)
    source_origin_ids = load_source_origin_story_ids(
        supabase_client, [story_id for story_id, _reason in candidates]
    )
    unpublishable = classify_unpublishable_rows(story_rows, source_origin_ids)
    reason_counts = Counter(reason for _story_id, reason in unpublishable)

    digest_by_story = _fetch_current_digests(
        supabase_client, [story_id for story_id, _reason in unpublishable]
    )
    to_retire = [
        (story_id, reason)
        for story_id, reason in unpublishable
        if story_id in digest_by_story
    ]

    print(f"stories scanned:             {len(story_rows)}")
    print(f"source-origin exempt:        {len(source_origin_ids)}")
    print(f"unpublishable headlines:     {len(unpublishable)}")
    for reason, count in sorted(reason_counts.items()):
        print(f"  {reason:<20} {count}")
    print(f"still placeable (to retire): {len(to_retire)}")
    for story_id, reason in to_retire:
        print(f"  {story_id}  digest={digest_by_story[story_id]}  {reason}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to retire these.")
        return 0
    if not to_retire:
        print("\nNothing to retire — already clean.")
        return 0
    if not _confirm_apply(len(to_retire), args.yes):
        print("\nAborted — nothing written.")
        return 1

    digest_ids = [digest_by_story[story_id] for story_id, _reason in to_retire]
    print(f"\nundo manifest (digest_id): {' '.join(digest_ids)}")
    updated_count = _retire_digests(supabase_client, digest_ids)
    logger.info(
        "unpublishable_headlines_retired",
        retired_count=updated_count,
        digest_ids=digest_ids,
    )
    if updated_count != len(digest_ids):
        # Reason: fail LOUD (Rule 12) — a short update means a digest was superseded
        # between the scan and the write, or a chunk silently no-opped. Reporting
        # success here would leave a partial prod mutation undetected.
        print(
            f"\nWARNING: expected to retire {len(digest_ids)} digest(s) but "
            f"{updated_count} row(s) were updated. Re-run the dry run to see what "
            "remains before assuming the cleanup landed."
        )
        return 1
    print(f"\nRetired {updated_count} story(ies).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
