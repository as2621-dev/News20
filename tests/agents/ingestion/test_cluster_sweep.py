"""Unit + integration tests for the shared X cluster sweep (Slice #23).

Every external is mocked at the boundary (CLAUDE.md §6): the batched xAI ``x_search``
call is an injected ``post_discoverer`` stub returning fixture post dicts (or raising
to simulate a provider failure), the LLM theme grouping is an injected
``theme_extractor`` stub, and the Supabase client is a fake capturing
``.table(...).select/upsert().execute()``. No network, no API key, no real DB.

Each test maps to an issue #23 acceptance criterion:
    • happy path — one batched call, >= 1 stored theme traceable to its handles/tweets
    • retweets filtered out BEFORE theme extraction
    • a single-handle-dominated conversation mints NO theme (multi-handle gate)
    • a silent cluster stores an HONEST empty sweep (no fabricated theme)
    • multiple followers of the same cluster → exactly ONE sweep per cluster per day
    • xAI failure → empty themes, loud log, NOTHING persisted (seam intact)

    >>> pytest tests/agents/ingestion/test_cluster_sweep.py -v
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from agents.ingestion.cluster_sweep import (
    MAX_SWEEP_HANDLES,
    ClusterSweepTarget,
    SweptPost,
    sweep_cluster,
    sweep_followed_clusters,
)
from agents.shared.settings import Settings

_SWEEP_DATE = date(2026, 7, 5)
_CLUSTER_ID = "cluster-frontier-labs"
_SETTINGS = Settings(xai_api_key="")


# ----------------------------------------------------------------------
# Fakes: Supabase client + injected seams
# ----------------------------------------------------------------------


class _FakeQuery:
    """Chainable stub for ``.select().eq().limit().execute()`` / ``.upsert().execute()``."""

    def __init__(self, table: "_FakeTable", op: str) -> None:
        self._table = table
        self._op = op

    def select(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def eq(self, column: str, value: Any) -> "_FakeQuery":
        self._table.selected_filters[column] = value
        return self

    def limit(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def upsert(self, row: dict[str, Any], **kwargs: Any) -> "_FakeQuery":
        self._table.upserted_rows.append(row)
        self._table.upsert_kwargs.append(kwargs)
        return self

    def execute(self) -> Any:
        if self._op == "select":
            cluster_id = self._table.selected_filters.get("cluster_id")
            sweep_date = self._table.selected_filters.get("sweep_date")
            key = (cluster_id, sweep_date)
            row = self._table.existing_rows.get(key)
            return type("Resp", (), {"data": [row] if row else []})()
        return type("Resp", (), {"data": self._table.upserted_rows})()


class _FakeTable:
    """Captures reads/writes for one table and can serve pre-seeded existing rows."""

    def __init__(self) -> None:
        self.selected_filters: dict[str, Any] = {}
        self.upserted_rows: list[dict[str, Any]] = []
        self.upsert_kwargs: list[dict[str, Any]] = []
        self.existing_rows: dict[tuple[Any, Any], dict[str, Any]] = {}

    def select(self, *args: Any, **kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self, "select").select(*args, **kwargs)

    def upsert(self, row: dict[str, Any], **kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self, "upsert").upsert(row, **kwargs)


class _FakeDb:
    """A one-table fake Supabase client (``x_cluster_sweeps``)."""

    def __init__(self) -> None:
        self.table_obj = _FakeTable()

    def table(self, _name: str) -> _FakeTable:
        return self.table_obj


def _discoverer(posts: list[dict[str, Any]], calls: list[int] | None = None):
    """Build a batched-x_search stub returning ``posts`` and counting invocations."""

    async def stub(
        handles: list[str], sweep_date: date, max_posts: int
    ) -> list[dict[str, Any]]:
        if calls is not None:
            calls.append(1)
        return posts

    return stub


def _raising_discoverer():
    async def stub(
        handles: list[str], sweep_date: date, max_posts: int
    ) -> list[dict[str, Any]]:
        raise RuntimeError("xAI 429 rate limited")

    return stub


def _extractor(themes: list[dict[str, Any]]):
    async def stub(posts: list[SweptPost]) -> list[dict[str, Any]]:
        return themes

    return stub


def _post(
    handle: str, tweet_id: str, text: str, is_retweet: bool = False
) -> dict[str, Any]:
    return {
        "tweet_url": f"https://x.com/{handle}/status/{tweet_id}",
        "author_handle": handle,
        "text": text,
        "published_utc": "2026-07-05T10:00:00+00:00",
        "is_retweet": is_retweet,
    }


def _target(handles: list[str]) -> ClusterSweepTarget:
    return ClusterSweepTarget(
        cluster_id=_CLUSTER_ID, cluster_slug="ai.frontier-labs", handles=handles
    )


async def _run(
    target: ClusterSweepTarget, db: _FakeDb, discoverer: Any, extractor: Any
):
    return await sweep_cluster(
        target,
        _SWEEP_DATE,
        db_client=db,
        post_discoverer=discoverer,
        theme_extractor=extractor,
        settings=_SETTINGS,
    )


# ----------------------------------------------------------------------
# AC: happy path — one batched call, >= 1 theme traceable to handles/tweets
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_one_call_yields_traceable_theme() -> None:
    """A followed cluster is swept in ONE call and stores >= 1 attributed theme.

    WHY: the whole slice's value is a shared, cheap sweep whose themes are traceable
    back to the real posts — an un-attributable theme is a fabrication downstream can't
    trust, so the theme must carry the exact handles + tweet URLs it was drawn from.
    """
    calls: list[int] = []
    posts = [
        _post("OpenAI", "1", "We are releasing a new reasoning model today."),
        _post("AnthropicAI", "2", "Our latest model improves on reasoning benchmarks."),
    ]
    themes = [
        {
            "theme_summary": "Frontier labs ship new reasoning models",
            "supporting_tweet_urls": [
                "https://x.com/OpenAI/status/1",
                "https://x.com/AnthropicAI/status/2",
            ],
        }
    ]
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]),
        db,
        _discoverer(posts, calls),
        _extractor(themes),
    )

    assert sum(calls) == 1  # exactly one batched x_search call
    assert len(result.themes) == 1
    theme = result.themes[0]
    # Traceable: handles recomputed from real posts, both handles attributed.
    assert set(theme.supporting_handles) == {"OpenAI", "AnthropicAI"}
    assert set(theme.supporting_tweet_urls) == {
        "https://x.com/OpenAI/status/1",
        "https://x.com/AnthropicAI/status/2",
    }
    # Persisted shared row carries the theme.
    assert len(db.table_obj.upserted_rows) == 1
    stored = db.table_obj.upserted_rows[0]
    assert stored["cluster_id"] == _CLUSTER_ID
    assert stored["sweep_date"] == "2026-07-05"
    assert stored["themes"][0]["theme_summary"].startswith("Frontier labs")


# ----------------------------------------------------------------------
# AC: retweets filtered out BEFORE theme extraction
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retweets_filtered_before_extraction() -> None:
    """Retweets are dropped before theme extraction — the extractor never sees them.

    WHY: retweets are amplification, not the handle's own signal; letting them reach
    theme extraction would let one viral RT masquerade as multi-handle convergence.
    """
    posts = [
        _post("OpenAI", "1", "Original OpenAI announcement."),
        _post(
            "AnthropicAI",
            "2",
            "RT @OpenAI: Original OpenAI announcement.",
            is_retweet=True,
        ),
        _post("AnthropicAI", "3", "Our own original take on the news."),
    ]
    seen_by_extractor: list[list[SweptPost]] = []

    async def capturing_extractor(original: list[SweptPost]) -> list[dict[str, Any]]:
        seen_by_extractor.append(original)
        return []

    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), capturing_extractor
    )

    assert result.raw_post_count == 3
    assert result.original_post_count == 2  # the retweet is gone
    extractor_input = seen_by_extractor[0]
    assert all(not p.is_retweet for p in extractor_input)
    assert {p.tweet_url for p in extractor_input} == {
        "https://x.com/OpenAI/status/1",
        "https://x.com/AnthropicAI/status/3",
    }


@pytest.mark.asyncio
async def test_retweet_detected_from_rt_prefix_backstop() -> None:
    """A post flagged only by its ``RT @`` text prefix is still filtered as a retweet.

    WHY: the original-only guarantee must not depend on the model setting is_retweet.
    """
    posts = [
        _post("OpenAI", "1", "Original."),
        {
            "tweet_url": "https://x.com/AnthropicAI/status/2",
            "author_handle": "AnthropicAI",
            "text": "RT @SomeoneElse: a hot take",
            "published_utc": "2026-07-05T10:00:00+00:00",
            # note: is_retweet flag absent — must be caught by the RT @ prefix
        },
    ]
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), _extractor([])
    )
    assert result.original_post_count == 1


@pytest.mark.asyncio
async def test_stringified_is_retweet_false_is_not_dropped() -> None:
    """A post whose ``is_retweet`` is the STRING "false" is kept (bool("false") trap).

    WHY: LLMs stringify booleans; ``bool("false")`` is True in Python, so without
    proper coercion a genuine original post would be silently dropped as a retweet.
    """
    posts = [
        {
            "tweet_url": "https://x.com/OpenAI/status/1",
            "author_handle": "OpenAI",
            "text": "A real original post.",
            "published_utc": "2026-07-05T10:00:00+00:00",
            "is_retweet": "false",  # a STRING, not a bool
        },
        {
            "tweet_url": "https://x.com/AnthropicAI/status/2",
            "author_handle": "AnthropicAI",
            "text": "Another real original post.",
            "published_utc": "2026-07-05T10:00:00+00:00",
            "is_retweet": "true",  # stringified True — must still be dropped
        },
    ]
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), _extractor([])
    )
    assert result.original_post_count == 1  # "false" kept, "true" dropped


@pytest.mark.asyncio
async def test_cached_read_tolerates_partial_stored_theme() -> None:
    """A stored theme missing its support lists rehydrates as empty lists, no crash.

    WHY: a legacy / partially-written row must degrade to a skip on the cached read
    path (which is outside the DB try/except), never raise a ValidationError.
    """
    db = _FakeDb()
    db.table_obj.existing_rows[(_CLUSTER_ID, "2026-07-05")] = {
        "cluster_id": _CLUSTER_ID,
        "sweep_date": "2026-07-05",
        "handle_count": 2,
        "raw_post_count": 3,
        "original_post_count": 3,
        # a theme with ONLY a summary — no supporting_handles / supporting_tweet_urls
        "themes": [{"theme_summary": "Legacy theme with no support lists"}],
    }
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer([]), _extractor([])
    )
    assert result.was_cached is True
    assert len(result.themes) == 1
    assert result.themes[0].supporting_handles == []
    assert result.themes[0].supporting_tweet_urls == []


# ----------------------------------------------------------------------
# AC: single-handle-dominated conversation mints NO theme (multi-handle gate)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_handle_domination_mints_no_theme() -> None:
    """A theme backed by only ONE handle's posts is dropped by the multi-handle gate.

    WHY: the shared-cluster value is CONVERGENCE across independent voices; one loud
    account's thread, however long, is that account's opinion — not a cluster theme.
    """
    posts = [
        _post("OpenAI", "1", "Thread 1/5 on our model."),
        _post("OpenAI", "2", "Thread 2/5 on our model."),
        _post("OpenAI", "3", "Thread 3/5 on our model."),
    ]
    themes = [
        {
            "theme_summary": "OpenAI thread about their model",
            "supporting_tweet_urls": [
                "https://x.com/OpenAI/status/1",
                "https://x.com/OpenAI/status/2",
                "https://x.com/OpenAI/status/3",
            ],
        }
    ]
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), _extractor(themes)
    )

    assert result.themes == []  # single-handle theme rejected
    # Honest empty sweep still stored (the cluster WAS swept, it just had no theme).
    assert len(db.table_obj.upserted_rows) == 1
    assert db.table_obj.upserted_rows[0]["themes"] == []


@pytest.mark.asyncio
async def test_theme_urls_not_in_swept_set_are_dropped() -> None:
    """Fabricated supporting URLs are stripped; a theme left single-handle is dropped.

    WHY: the model must not be able to invent attribution — only real swept posts count
    toward the multi-handle gate, so a hallucinated second handle cannot rescue a theme.
    """
    posts = [
        _post("OpenAI", "1", "Original OpenAI post."),
        _post("AnthropicAI", "2", "Original Anthropic post."),
    ]
    themes = [
        {
            "theme_summary": "A theme citing one real and one invented tweet",
            "supporting_tweet_urls": [
                "https://x.com/OpenAI/status/1",
                "https://x.com/GhostHandle/status/999",  # not in the swept set
            ],
        }
    ]
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), _extractor(themes)
    )
    # Only OpenAI/1 is real → 1 distinct handle → below the gate → dropped.
    assert result.themes == []


# ----------------------------------------------------------------------
# AC: silent cluster → honest empty sweep (no fabricated theme)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_cluster_stores_honest_empty() -> None:
    """A cluster with no original posts stores an empty sweep — no fabricated theme.

    WHY: honesty over coverage — an empty day is a real answer the feed must be able to
    read as "nothing today," never a made-up theme to fill the slot.
    """
    extractor_calls: list[int] = []

    async def counting_extractor(posts: list[SweptPost]) -> list[dict[str, Any]]:
        extractor_calls.append(1)
        return []

    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer([]), counting_extractor
    )

    assert result.original_post_count == 0
    assert result.themes == []
    assert not extractor_calls  # extraction is never even attempted on a silent cluster
    assert len(db.table_obj.upserted_rows) == 1  # empty sweep IS persisted (honest)
    assert db.table_obj.upserted_rows[0]["themes"] == []


# ----------------------------------------------------------------------
# AC: multiple followers of the same cluster → one sweep per cluster per day
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_followers_one_sweep_per_day_via_cache() -> None:
    """A second sweep of the same cluster/day reuses the stored row — no second call.

    WHY: cost must stay flat with user growth; N followers of a cluster must share ONE
    sweep, so the once-per-day existence check short-circuits before any xAI call.
    """
    calls: list[int] = []
    posts = [
        _post("OpenAI", "1", "Original."),
        _post("AnthropicAI", "2", "Original."),
    ]
    themes = [
        {
            "theme_summary": "Convergence",
            "supporting_tweet_urls": [
                "https://x.com/OpenAI/status/1",
                "https://x.com/AnthropicAI/status/2",
            ],
        }
    ]
    db = _FakeDb()
    # First follower's build sweeps.
    first = await _run(
        _target(["OpenAI", "AnthropicAI"]),
        db,
        _discoverer(posts, calls),
        _extractor(themes),
    )
    assert sum(calls) == 1
    assert not first.was_cached

    # Seed the "existing row" the way the DB would have it after the first upsert.
    stored = db.table_obj.upserted_rows[0]
    db.table_obj.existing_rows[(_CLUSTER_ID, "2026-07-05")] = stored

    # Second follower's build for the SAME cluster/day: reuse, NO new call.
    second = await _run(
        _target(["OpenAI", "AnthropicAI"]),
        db,
        _discoverer(posts, calls),
        _extractor(themes),
    )
    assert sum(calls) == 1  # still one — no second x_search
    assert second.was_cached
    assert len(second.themes) == 1


@pytest.mark.asyncio
async def test_batch_dedups_duplicate_targets_in_run() -> None:
    """Two targets for the same cluster in one batch are swept once (in-run dedup).

    WHY: the same cluster can appear via two followers' target lists in one run; the
    batch must not double-call the provider for it.
    """
    calls: list[int] = []
    posts = [
        _post("OpenAI", "1", "Original."),
        _post("AnthropicAI", "2", "Original."),
    ]
    db = _FakeDb()
    targets = [_target(["OpenAI", "AnthropicAI"]), _target(["OpenAI", "AnthropicAI"])]
    results = await sweep_followed_clusters(
        targets,
        _SWEEP_DATE,
        db_client=db,
        post_discoverer=_discoverer(posts, calls),
        theme_extractor=_extractor([]),
        settings=_SETTINGS,
    )
    assert len(results) == 1
    assert sum(calls) == 1


# ----------------------------------------------------------------------
# AC: xAI failure → empty themes, loud log, NOTHING persisted (seam intact)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_failure_empty_and_unpersisted() -> None:
    """A provider failure yields empty themes and persists NOTHING (retry-able day).

    WHY: a transient xAI outage must not poison the day's shared row (which would block
    a retry) nor crash the batch — downstream simply falls to the news floor.
    """
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _raising_discoverer(), _extractor([])
    )

    assert result.provider_failed is True
    assert result.themes == []
    assert db.table_obj.upserted_rows == []  # nothing written — no half/poisoned row


@pytest.mark.asyncio
async def test_theme_extraction_failure_stores_honest_themeless_row() -> None:
    """If grouping fails AFTER posts were found, store the posts with zero themes.

    WHY: the sweep succeeded and found real posts; that is honest signal worth
    recording (counts), and an empty theme list is the honest degradation.
    """
    posts = [
        _post("OpenAI", "1", "Original."),
        _post("AnthropicAI", "2", "Original."),
    ]

    async def raising_extractor(original: list[SweptPost]) -> list[dict[str, Any]]:
        raise RuntimeError("xAI grouping 500")

    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), raising_extractor
    )

    assert result.provider_failed is False
    assert result.themes == []
    assert result.original_post_count == 2
    assert len(db.table_obj.upserted_rows) == 1
    assert db.table_obj.upserted_rows[0]["original_post_count"] == 2


# ----------------------------------------------------------------------
# Deterministic guards: handle cap, off-handle drop, upsert conflict key
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handles_capped_and_deduped_under_limit() -> None:
    """Handles are deduped (case-insensitive) and capped under the 20-handle limit.

    WHY: exceeding the live-verified x_search cap makes the batched call fail; the cap
    is a deterministic guard, not something to trust the caller to respect.
    """
    handles = [f"Handle{i}" for i in range(30)] + ["handle0"]  # 30 uniques + 1 dup
    captured: list[list[str]] = []

    async def capturing_discoverer(
        hs: list[str], d: date, m: int
    ) -> list[dict[str, Any]]:
        captured.append(hs)
        return []

    db = _FakeDb()
    result = await _run(_target(handles), db, capturing_discoverer, _extractor([]))
    assert len(captured[0]) == MAX_SWEEP_HANDLES
    assert result.handle_count == MAX_SWEEP_HANDLES


@pytest.mark.asyncio
async def test_off_handle_posts_are_not_attributed() -> None:
    """A post from a handle NOT in the cluster is dropped, never attributed.

    WHY: the provider payload is untrusted; an off-handle post must not count toward a
    theme's multi-handle support.
    """
    posts = [
        _post("OpenAI", "1", "Original."),
        _post("RandomStranger", "2", "Off-cluster noise."),
    ]
    db = _FakeDb()
    result = await _run(
        _target(["OpenAI", "AnthropicAI"]), db, _discoverer(posts), _extractor([])
    )
    assert result.raw_post_count == 1  # only the in-cluster OpenAI post survives


@pytest.mark.asyncio
async def test_upsert_uses_cluster_date_conflict_key() -> None:
    """The store upserts on (cluster_id, sweep_date) so a re-run overwrites in place.

    WHY: the once-per-day idempotency depends on the upsert targeting the unique key,
    not inserting a duplicate row.
    """
    db = _FakeDb()
    await _run(_target(["OpenAI", "AnthropicAI"]), db, _discoverer([]), _extractor([]))
    assert db.table_obj.upsert_kwargs[0]["on_conflict"] == "cluster_id,sweep_date"


@pytest.mark.asyncio
async def test_no_handles_is_clean_noop_without_call() -> None:
    """A cluster with no X handles is a no-op: no provider call, honest empty stored."""
    calls: list[int] = []
    db = _FakeDb()
    result = await _run(_target([]), db, _discoverer([], calls), _extractor([]))
    assert sum(calls) == 0
    assert result.original_post_count == 0
    assert len(db.table_obj.upserted_rows) == 1
