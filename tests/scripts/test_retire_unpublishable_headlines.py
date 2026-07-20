"""Tests for the one-time masthead-headline cleanup script (slice #62).

WHY (Rule 9 — encode the contract, not the call shape):
  • The script's job is to retire ONLY rows the pipeline itself would refuse to
    publish. If it ever developed a second opinion of "bad headline", it would
    delete reels the pipeline is happy to ship — so the classifier is asserted
    against the same cases the shared predicate defines.
  • It writes to production, so "dry-run does not write" and "a second run is a
    no-op" are safety contracts, not conveniences: a non-idempotent cleanup that
    re-flips already-superseded digests would rewrite digest history.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

import scripts.retire_unpublishable_headlines as retire_script
from scripts.retire_unpublishable_headlines import (
    _fetch_all_story_rows,
    _fetch_current_digests,
    _retire_digests,
    classify_unpublishable_rows,
)


def _story_row(story_id: str, headline: Any, outlet: str | None) -> dict[str, Any]:
    return {
        "story_id": story_id,
        "story_headline": headline,
        "story_primary_outlet_name": outlet,
    }


def test_classify_flags_masthead_and_fragment_but_not_real_headlines() -> None:
    """Masthead + too-short + empty are flagged; a genuine headline is left alone."""
    rows = [
        _story_row("s-masthead", "Language Magazine", "Language Magazine"),
        _story_row("s-fragment", "Breaking", "Some Outlet"),
        _story_row("s-empty", None, "Some Outlet"),
        _story_row("s-good", "Fed holds rates steady", "Reuters"),
        _story_row("s-short-but-real", "Assad flees Syria", "Reuters"),
    ]
    assert classify_unpublishable_rows(rows) == [
        ("s-masthead", "title_equals_outlet"),
        ("s-fragment", "too_few_words"),
        ("s-empty", "empty"),
    ]


def test_classify_on_empty_input_returns_empty() -> None:
    """No rows → nothing to retire (the post-cleanup steady state)."""
    assert classify_unpublishable_rows([]) == []


class _RecordingQuery:
    """Chainable query stand-in that records the update payload + filters applied."""

    def __init__(self, rows: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._calls = calls
        self._record: dict[str, Any] | None = None

    def select(self, *_args: object, **_kwargs: object) -> "_RecordingQuery":
        return self

    def order(self, *_args: object, **_kwargs: object) -> "_RecordingQuery":
        return self

    def range(self, start: int, end: int) -> "_RecordingQuery":
        self._rows = self._rows[start : end + 1]
        return self

    def update(self, payload: dict[str, Any]) -> "_RecordingQuery":
        self._record = {"payload": payload, "filters": {}}
        self._calls.append(self._record)
        return self

    def in_(self, column: str, values: list[str]) -> "_RecordingQuery":
        if self._record is not None:
            self._record["filters"][column] = values
        return self

    def eq(self, column: str, value: object) -> "_RecordingQuery":
        if self._record is not None:
            self._record["filters"][column] = value
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self._tables = tables
        self.update_calls: list[dict[str, Any]] = []

    def table(self, table_name: str) -> _RecordingQuery:
        return _RecordingQuery(
            list(self._tables.get(table_name, [])), self.update_calls
        )


class _StatefulQuery:
    """Query stand-in whose ``update`` really mutates the backing rows."""

    def __init__(self, rows: list[dict[str, Any]], client: "_StatefulSupabase") -> None:
        self._rows = rows
        self._client = client
        self._payload: dict[str, Any] | None = None
        self._filters: dict[str, Any] = {}

    def select(self, *_args: object, **_kwargs: object) -> "_StatefulQuery":
        return self

    def order(self, *_args: object, **_kwargs: object) -> "_StatefulQuery":
        return self

    def range(self, start: int, end: int) -> "_StatefulQuery":
        self._filters["__range"] = (start, end)
        return self

    def update(self, payload: dict[str, Any]) -> "_StatefulQuery":
        self._payload = payload
        return self

    def in_(self, column: str, values: list[str]) -> "_StatefulQuery":
        self._filters[column] = values
        return self

    def eq(self, column: str, value: object) -> "_StatefulQuery":
        self._filters[column] = value
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for column, expected in self._filters.items():
            if column == "__range":
                continue
            if isinstance(expected, list):
                if row.get(column) not in expected:
                    return False
            elif row.get(column) != expected:
                return False
        return True

    def execute(self) -> SimpleNamespace:
        matched = [row for row in self._rows if self._matches(row)]
        if self._payload is not None:
            self._client.update_calls.append(
                {"payload": self._payload, "filters": dict(self._filters)}
            )
            for row in matched:
                row.update(self._payload)
            return SimpleNamespace(data=matched)
        window = self._filters.get("__range")
        if window is not None:
            start, end = window
            matched = matched[start : end + 1]
        return SimpleNamespace(data=matched)


class _StatefulSupabase:
    """Client stand-in backed by mutable row lists, so writes are observable."""

    def __init__(
        self,
        stories: list[dict[str, Any]] | None = None,
        digests: list[dict[str, Any]] | None = None,
        story_sources: list[dict[str, Any]] | None = None,
    ) -> None:
        self.stories = stories or []
        self.digests = digests or []
        self.story_sources = story_sources or []
        self.update_calls: list[dict[str, Any]] = []

    def table(self, table_name: str) -> _StatefulQuery:
        rows = {
            "stories": self.stories,
            "digests": self.digests,
            "story_sources": self.story_sources,
        }[table_name]
        return _StatefulQuery(rows, self)


def test_dry_run_path_reads_without_issuing_any_update() -> None:
    """The read half of a dry run touches no writer — the safety default."""
    client = _FakeSupabase(
        {
            "stories": [
                _story_row("s-masthead", "Language Magazine", "Language Magazine")
            ],
            "digests": [{"digest_id": "digest-1", "digest_story_id": "s-masthead"}],
        }
    )
    rows = _fetch_all_story_rows(client)
    unpublishable = classify_unpublishable_rows(rows)
    placeable = _fetch_current_digests(
        client, [story_id for story_id, _reason in unpublishable]
    )
    assert placeable == {"s-masthead": "digest-1"}
    assert client.update_calls == []


def test_retire_targets_explicit_digest_ids_and_only_current_ones() -> None:
    """The update names digest_ids and is filtered on digest_is_current=true.

    Targeting digest_id (not story_id) is what stops a newer digest produced between
    the scan and the confirmation prompt from being clobbered; the is_current filter is
    what makes a second run a no-op instead of rewriting superseded digest history.
    """
    client = _FakeSupabase({"digests": []})
    _retire_digests(client, ["digest-1", "digest-2"])
    assert client.update_calls == [
        {
            "payload": {"digest_is_current": False},
            "filters": {
                "digest_id": ["digest-1", "digest-2"],
                "digest_is_current": True,
            },
        }
    ]


def test_retire_returns_the_number_of_rows_actually_updated() -> None:
    """A short update must be reported, not assumed successful (Rule 12).

    The caller compares this against the manifest length and warns loudly, so a
    partial prod mutation can never be printed as a clean success.
    """
    client = _FakeSupabase({"digests": [{"digest_id": "digest-1"}]})
    assert _retire_digests(client, ["digest-1", "digest-2"]) == 1


def test_retire_with_no_targets_issues_no_update() -> None:
    """A clean database → zero writes, so re-running the cleanup is free."""
    client = _FakeSupabase({"digests": []})
    _retire_digests(client, [])
    assert client.update_calls == []


def _scan_to_retire(client: _StatefulSupabase) -> dict[str, str]:
    """The script's read half: story_id -> digest_id for what a run would retire."""
    unpublishable = classify_unpublishable_rows(_fetch_all_story_rows(client))
    return _fetch_current_digests(
        client, [story_id for story_id, _reason in unpublishable]
    )


def test_a_second_run_after_retiring_finds_nothing_left_to_do() -> None:
    """True idempotency: scan -> retire -> scan again yields an empty to-retire set.

    Asserted against a digests table that actually applies the update, so removing the
    ``digest_is_current`` filter from ``_retire_digests`` — or targeting the wrong
    column — makes this fail. The bad headline is deliberately still in ``stories``:
    what makes the story stop being a target is that it is no longer placeable.
    """
    client = _StatefulSupabase(
        stories=[_story_row("s-masthead", "Language Magazine", "Language Magazine")],
        digests=[
            {
                "digest_id": "digest-1",
                "digest_story_id": "s-masthead",
                "digest_is_current": True,
            }
        ],
    )
    first_pass = _scan_to_retire(client)
    assert first_pass == {"s-masthead": "digest-1"}

    assert _retire_digests(client, list(first_pass.values())) == 1

    assert _scan_to_retire(client) == {}
    assert _retire_digests(client, ["digest-1"]) == 0


def test_dry_run_through_main_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` with no flags must complete without issuing a single update.

    Guards the ordering of the ``--apply`` check itself: moving that guard below the
    write would still leave every unit test green while mutating production.
    """
    client = _StatefulSupabase(
        stories=[_story_row("s-masthead", "Language Magazine", "Language Magazine")],
        digests=[
            {
                "digest_id": "digest-1",
                "digest_story_id": "s-masthead",
                "digest_is_current": True,
            }
        ],
    )
    # Placeholder credentials only — the client is mocked, nothing connects out.
    monkeypatch.setenv("SUPABASE_URL", "https://test.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-a-secret")
    with (
        patch.object(sys, "argv", ["retire_unpublishable_headlines.py"]),
        patch("supabase.create_client", return_value=client),
        patch("dotenv.load_dotenv"),
    ):
        assert retire_script.main() == 0
    assert client.update_calls == []
    assert client.digests[0]["digest_is_current"] is True


def test_classify_matches_a_masthead_written_as_the_outlet_domain() -> None:
    """The outlet column is a domain for some rows — the classifier must still match.

    Keeps the script's verdict identical to the loader's, which offers the same value
    as both display name and domain. If they diverged, a retired story could still
    load, or a loadable one be retired.
    """
    rows = [
        _story_row("s-domain", "Modern Farmer Magazine", "modernfarmermagazine.com"),
        _story_row("s-good", "Fed holds rates steady", "reuters.com"),
    ]
    assert classify_unpublishable_rows(rows) == [("s-domain", "title_equals_outlet")]


def test_classify_never_retires_a_followed_source_reel() -> None:
    """A YouTube/X reel with a short creator-written title is EXEMPT, not retired.

    Source reels carry the creator's own upload title, not a scraped <PAGE_TITLE>, so
    the masthead gate does not apply to them — the produce gate and the write-time gate
    both carry this same exemption. Without it, a one-word upload title ("Ferrari")
    would have its digest retired and the user would silently lose a reel they
    explicitly subscribed to.
    """
    rows = [
        _story_row("s-youtube", "Ferrari", "Donut Media"),
        _story_row("s-news", "Ferrari", "autoweek.com"),
    ]
    assert classify_unpublishable_rows(rows, {"s-youtube"}) == [
        ("s-news", "too_few_words")
    ]
