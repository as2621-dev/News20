"""Regression test for the Nitish-eviction bug (phase-SP2).

WHY this test exists (Rule 9 — encode intent, not mechanics): a re-run of the
source-reel producer with an EMPTY produce pool used to silently evict the prior
``feed_slot_kind='source'`` reels and back-fill their slots with topic reels (the
non-transactional delete-then-insert in ``_rebuild_feed`` dropped existing source
rows, re-placing source slots ONLY from THIS run's produced ids). The fix (SP1)
carries existing source rows forward; SP2 makes the write fail-safe (abort instead
of delete on a source-reel regression) and timestamps the backup so a same-day
re-run never clobbers the prior recovery copy.

The three tests below assert the SURVIVAL INTENT, so each FAILS if SP1/SP2 are
reverted:

  - ``test_source_reels_survive_empty_second_run`` — run-1 produces N source
    reels; run-2 (empty pool) must keep all N at source positions, none replaced
    by topic reels. FAILS if SP1's carry-forward (``carried_source_rows``) is
    reverted to the old ``continue`` drop.
  - ``test_two_runs_produce_distinct_backups`` — two rebuilds leave two distinct
    ``/tmp/ash_feed_backup_<date>_*.json`` files. FAILS if SP2's epoch+pid suffix
    is reverted to the fixed same-day-clobbering name.
  - ``test_shrinking_rebuild_aborts_without_delete`` — a rebuild that would shrink
    the source-reel count aborts: NO delete, NO insert, exit ``1``, structured
    ``feed_rebuild_aborted`` log. FAILS if SP2's fail-safe guard is reverted.

All Supabase access is mocked at the client boundary (CLAUDE.md mocking strategy);
no real service is hit. The ``/tmp`` backup writes are redirected into a tmp dir
so no test litter is left behind.
"""

from __future__ import annotations

import glob
import os
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

import scripts.produce_source_reels as mod

ASH_UID = mod.ASH_UID
TARGET = date(2026, 6, 18)
FEED_DATE_ISO = TARGET.isoformat()


class _DailyFeedsQuery:
    """Chainable stub for a ``daily_feeds`` SELECT.

    Every builder method returns self; ``execute`` returns the rows the parent
    table currently holds (so a run-2 SELECT sees run-1's inserted rows). The
    ``daily_feeds`` rows are sourced live from the owning table so writes between
    runs are observed by subsequent reads.
    """

    def __init__(self, rows_provider) -> None:
        self._rows_provider = rows_provider

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        # Return a shallow copy so callers can't mutate the table's backing store.
        return SimpleNamespace(data=[dict(r) for r in self._rows_provider()])


class _DeleteQuery:
    """Chainable stub for ``daily_feeds.delete().eq().eq().execute()`` that records
    that a delete fired and clears the table's rows when executed."""

    def __init__(self, on_delete) -> None:
        self._on_delete = on_delete

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        self._on_delete()
        return SimpleNamespace(data=[])


class _StaticQuery:
    """Chainable stub returning a fixed row list (for allocation/interests)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(r) for r in self._rows])


class _FakeSupabase:
    """Minimal observable Supabase client for ``_rebuild_feed``.

    Holds a mutable ``daily_feeds`` row store so a delete clears it and an insert
    repopulates it — letting a second rebuild observe the first run's writes.
    Records every ``delete`` and ``insert`` so the abort path can be proven to do
    NEITHER. ``user_feed_allocation`` and ``interests`` are static fixtures.
    """

    def __init__(
        self,
        daily_feeds_rows: list[dict[str, Any]],
        allocation_rows: list[dict[str, Any]],
        interest_rows: list[dict[str, Any]],
    ) -> None:
        self.daily_feeds_rows = [dict(r) for r in daily_feeds_rows]
        self._allocation_rows = allocation_rows
        self._interest_rows = interest_rows
        self.delete_calls = 0
        self.insert_calls: list[list[dict[str, Any]]] = []

    def _on_delete(self) -> None:
        self.delete_calls += 1
        self.daily_feeds_rows = []

    def _insert(self, rows: list[dict[str, Any]]) -> Any:
        self.insert_calls.append([dict(r) for r in rows])
        self.daily_feeds_rows = [dict(r) for r in rows]
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    def table(self, name: str):
        if name == "daily_feeds":
            return SimpleNamespace(
                select=lambda *_a, **_k: _DailyFeedsQuery(lambda: self.daily_feeds_rows),
                delete=lambda *_a, **_k: _DeleteQuery(self._on_delete),
                insert=lambda rows, *_a, **_k: self._insert(rows),
            )
        if name == "user_feed_allocation":
            return _StaticQuery(self._allocation_rows)
        if name == "interests":
            return _StaticQuery(self._interest_rows)
        raise AssertionError(f"unexpected table queried: {name}")


def _source_row(story_id: str, position: int) -> dict[str, Any]:
    """A live ``feed_slot_kind='source'`` row (a previously-produced source reel)."""
    return {
        "feed_story_id": story_id,
        "feed_score": 1.0,
        "feed_matched_interest_id": None,
        "feed_slot_kind": "source",
        "feed_position": position,
    }


def _topic_row(story_id: str, interest_id: str, position: int) -> dict[str, Any]:
    """A live topic (interest) row that the rebuild buckets by category."""
    return {
        "feed_story_id": story_id,
        "feed_score": 0.5,
        "feed_matched_interest_id": interest_id,
        "feed_slot_kind": "interest",
        "feed_position": position,
    }


def _allocation_rows() -> list[dict[str, Any]]:
    """Source slots (1 youtube + 1 x) plus a topic category with spare budget.

    The ``sport`` budget is large enough to back-fill toward 30, so if the source
    slots were (buggily) left for topic reels the test would SEE topic reels in
    those positions — making the eviction observable.
    """
    return [
        {"allocation_category": "youtube", "allocation_slot_count": 1, "allocation_sort_order": 1},
        {"allocation_category": "x", "allocation_slot_count": 1, "allocation_sort_order": 2},
        {"allocation_category": "sport", "allocation_slot_count": 28, "allocation_sort_order": 3},
    ]


def _interest_rows() -> list[dict[str, Any]]:
    """One interest whose slug roots to the ``sport`` screen category."""
    return [{"interest_id": "int-sport", "interest_slug": "sport.cricket"}]


@pytest.fixture(autouse=True)
def _redirect_tmp_backups(tmp_path, monkeypatch):
    """Redirect ``/tmp/ash_feed_backup_*`` writes into the test's tmp dir.

    ``_rebuild_feed`` hard-codes ``/tmp/ash_feed_backup_...``; patch ``open`` and
    ``os.path.exists`` in the module so the real backup filename (still the
    SP2-suffixed pattern) is created under ``tmp_path`` instead. Returns the dir so
    the distinct-backup test can glob it. This keeps the SP2 naming intent under
    test while leaving no litter in the real ``/tmp``.
    """
    backup_dir = tmp_path / "tmp_backups"
    backup_dir.mkdir()
    real_open = open

    def _redirected_open(file, *args, **kwargs):
        if isinstance(file, str) and file.startswith("/tmp/ash_feed_backup_"):
            file = str(backup_dir / os.path.basename(file))
        return real_open(file, *args, **kwargs)

    real_exists = os.path.exists

    def _redirected_exists(path):
        if isinstance(path, str) and path.startswith("/tmp/ash_feed_backup_"):
            return real_exists(str(backup_dir / os.path.basename(path)))
        return real_exists(path)

    monkeypatch.setattr(mod, "open", _redirected_open, raising=False)
    monkeypatch.setattr(mod.os.path, "exists", _redirected_exists)
    return backup_dir


def test_source_reels_survive_empty_second_run() -> None:
    """Run-1 produces 2 source reels; run-2 (EMPTY pool) must keep both.

    Encodes SP1's carry-forward intent: with NOTHING produced this run, the two
    prior source reels must still be present at ``feed_slot_kind='source'``
    positions after the rebuild — never replaced by topic back-fill. Reverting the
    carry-forward (the old ``continue`` drop) makes both source reels vanish and
    this assertion fails.
    """
    # Live feed has 2 source reels (from run-1) + several topic reels.
    daily_feeds_rows = [
        _source_row("sp3-yt1", 1),
        _source_row("sp3-x1", 2),
    ] + [_topic_row(f"sp3-topic{i}", "int-sport", 2 + i) for i in range(1, 6)]

    supabase = _FakeSupabase(daily_feeds_rows, _allocation_rows(), _interest_rows())

    # Run-2: empty produce pool (the throttle / verification-halt scenario).
    return_code = mod._rebuild_feed(supabase, TARGET, produced_yt=[], produced_x=[])

    assert return_code == 0, "non-regressing carry-forward rebuild must succeed"
    # The write proceeded (this is NOT the abort case — count is preserved, not shrunk).
    assert supabase.delete_calls == 1
    assert len(supabase.insert_calls) == 1
    inserted = supabase.insert_calls[0]

    source_ids_after = {
        r["feed_story_id"] for r in inserted if r["feed_slot_kind"] == "source"
    }
    assert source_ids_after == {"sp3-yt1", "sp3-x1"}, (
        "both prior source reels must survive an empty run; missing ids means the "
        "carry-forward (SP1) was lost and topic reels evicted them"
    )
    # The source reels occupy source-kind slots (not silently demoted to topic).
    assert sum(1 for r in inserted if r["feed_slot_kind"] == "source") == 2


def test_two_runs_produce_distinct_backups(_redirect_tmp_backups) -> None:
    """Two consecutive rebuilds must leave TWO distinct backup files.

    Encodes SP2's non-clobbering backup intent: the old fixed
    ``ash_feed_backup_<date>.json`` opened ``"w"`` destroyed the only recovery copy
    on the second same-day run. The epoch+pid(+counter) suffix must yield two
    distinct files. Reverting to the fixed name collapses the glob to ONE file and
    this assertion fails.
    """
    backup_dir = _redirect_tmp_backups

    def _fresh_supabase() -> _FakeSupabase:
        rows = [_source_row("sp3-yt1", 1), _source_row("sp3-x1", 2)]
        return _FakeSupabase(rows, _allocation_rows(), _interest_rows())

    mod._rebuild_feed(_fresh_supabase(), TARGET, produced_yt=[], produced_x=[])
    mod._rebuild_feed(_fresh_supabase(), TARGET, produced_yt=[], produced_x=[])

    backups = glob.glob(str(backup_dir / f"ash_feed_backup_{FEED_DATE_ISO}_*.json"))
    assert len(backups) == 2, (
        f"two rebuilds must produce two distinct backups, got {backups!r}; a single "
        "file means SP2's non-clobbering suffix was reverted"
    )
    assert len(set(backups)) == 2


def test_shrinking_rebuild_aborts_without_delete(monkeypatch) -> None:
    """A rebuild that would SHRINK the source-reel count must abort, not delete.

    Encodes SP2's fail-safe-write intent. Force a source shrink by giving the
    allocation ZERO youtube/x budget while 2 prior source reels exist and the
    produce pool is empty — the proposed feed would have 0 source reels (< 2). The
    rebuild MUST: (a) fire NO delete and NO insert (daily_feeds untouched), (b)
    return exit code 1, (c) emit the structured ``feed_rebuild_aborted`` log with
    the documented fields. Reverting the guard lets the delete-then-insert run and
    the source reels are wiped — flipping every assertion below.

    The structured log is captured by spying on the module logger's ``error``
    (deterministic — structlog's stdlib JSON handler does not propagate cleanly to
    pytest's ``caplog``, so spying at the call boundary is the reliable assert).
    """
    daily_feeds_rows = [
        _source_row("sp3-yt1", 1),
        _source_row("sp3-x1", 2),
        _topic_row("sp3-topicA", "int-sport", 3),
    ]
    # Allocation with NO youtube/x slots → the rebuild can place 0 source reels.
    allocation_rows = [
        {"allocation_category": "sport", "allocation_slot_count": 30, "allocation_sort_order": 1},
    ]
    supabase = _FakeSupabase(daily_feeds_rows, allocation_rows, _interest_rows())

    error_calls: list[tuple[str, dict[str, Any]]] = []
    real_error = mod.logger.error

    def _spy_error(event: str, **fields: Any) -> Any:
        error_calls.append((event, fields))
        return real_error(event, **fields)

    monkeypatch.setattr(mod.logger, "error", _spy_error)

    return_code = mod._rebuild_feed(supabase, TARGET, produced_yt=[], produced_x=[])

    # (b) exit non-zero.
    assert return_code == 1, "a source-reel-shrinking rebuild must exit 1, not write"
    # (a) NO delete and NO insert — daily_feeds left completely untouched.
    assert supabase.delete_calls == 0, "abort must NOT delete the live feed"
    assert supabase.insert_calls == [], "abort must NOT insert a shrunk feed"
    # The live rows are still present (proves no destructive write happened).
    assert len(supabase.daily_feeds_rows) == 3

    # (c) structured fail-loud log with the documented fields.
    aborts = [fields for event, fields in error_calls if event == "feed_rebuild_aborted"]
    assert aborts, "the abort must emit a 'feed_rebuild_aborted' structured log"
    fields = aborts[0]
    assert fields.get("fail_loud") is True
    assert fields.get("current_source_row_count") == 2
    assert fields.get("new_source_row_count") == 0
    assert fields.get("feed_date") == FEED_DATE_ISO
    assert fields.get("backup_path"), "abort log must record the backup_path"
