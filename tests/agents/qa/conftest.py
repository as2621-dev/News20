"""Shared fixtures for the grounded-Q&A corpus loader tests (Phase 2b SP1).

The Supabase client is mocked **at the boundary** (CLAUDE.md mandate): no
network, no key, no cost. :class:`FakeSupabaseClient` reproduces the exact
fluent read chain ``corpus.py`` uses —
``.table(name).select(...).eq(col, val).order(col).execute()`` — and crucially
**enforces the ``.eq(story_id_col, val)`` filter** so a test can prove the
corpus is scoped to one story (a row keyed to another ``story_id`` must never
appear). It records every ``(table, eq-filters)`` read so the
no-LLM/no-network boundary and the per-story scope are both assertable.

The fixture data is two stories — ``s1`` (the grounded story under test, with 3
ordered ``detail_chunks`` deliberately seeded out of insertion order) and ``s2``
(a decoy whose chunk must never leak into ``s1``'s corpus, Rule 9).
"""

from __future__ import annotations

from typing import Any

import pytest

# Reason: the story_id-filter column per table — the FakeSupabaseClient enforces
# this eq() filter so a corpus read can only ever return its own story's rows.
_STORY_FILTER_COLUMN = {
    "detail_chunks": "detail_story_id",
    "story_timeline": "timeline_story_id",
    "caption_sentences": "caption_story_id",
    "story_sources": "source_story_id",
}


class _FakeQuery:
    """A recording fluent query that mimics one Supabase ``.table()`` chain.

    Collects ``.eq()`` filters and an optional ``.order()`` column, then on
    ``.execute()`` filters the table's seeded rows by every eq-filter and sorts
    by the order column. Returns an object with a ``.data`` attribute (the shape
    ``corpus._rows`` reads), exactly like ``supabase-py``.
    """

    def __init__(
        self, table_name: str, rows: list[dict[str, Any]], log: list[dict[str, Any]]
    ) -> None:
        self._table_name = table_name
        self._rows = rows
        self._log = log
        self._eq_filters: dict[str, Any] = {}
        self._order_column: str | None = None

    def select(self, *_columns: str) -> _FakeQuery:
        return self

    def eq(self, column: str, value: Any) -> _FakeQuery:
        self._eq_filters[column] = value
        return self

    def order(self, column: str, desc: bool = False) -> _FakeQuery:
        self._order_column = column
        return self

    def limit(self, _count: int) -> _FakeQuery:
        return self

    def execute(self) -> Any:
        self._log.append({"table": self._table_name, "eq": dict(self._eq_filters)})
        filtered = [
            row
            for row in self._rows
            if all(row.get(col) == val for col, val in self._eq_filters.items())
        ]
        if self._order_column is not None:
            filtered = sorted(filtered, key=lambda row: row.get(self._order_column, 0))
        return type("FakeResponse", (), {"data": filtered})()


class FakeSupabaseClient:
    """A boundary mock of the Supabase client for corpus reads.

    Seeded with ``{table_name: [row, ...]}``. ``.table(name)`` returns a
    recording :class:`_FakeQuery`. Every ``.execute()`` is logged on
    ``read_log`` so a test can assert which tables were read and with which
    ``story_id`` filter — the boundary-isolation + per-story-scope proof.
    """

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self._tables = tables
        self.read_log: list[dict[str, Any]] = []

    def table(self, table_name: str) -> _FakeQuery:
        return _FakeQuery(table_name, self._tables.get(table_name, []), self.read_log)


def _make_seed_tables() -> dict[str, list[dict[str, Any]]]:
    """Build the two-story seed (s1 under test + s2 decoy for the leak test).

    s1's ``detail_chunks`` are seeded **out of order** (indices 2, 0, 1) so the
    chunk-ordering assertion is meaningful — the loader must re-order by
    ``chunk_index``, not trust insertion order.
    """
    return {
        "detail_chunks": [
            {
                "detail_story_id": "s1",
                "chunk_index": 2,
                "chunk_text": "S1 third paragraph about Hormuz shipping.",
            },
            {
                "detail_story_id": "s1",
                "chunk_index": 0,
                "chunk_text": "S1 first paragraph: Iran threatens the strait.",
            },
            {
                "detail_story_id": "s1",
                "chunk_index": 1,
                "chunk_text": "S1 second paragraph on oil prices.",
            },
            # Decoy: must NEVER appear in s1's corpus (Rule 9).
            {
                "detail_story_id": "s2",
                "chunk_index": 0,
                "chunk_text": "S2 DECOY paragraph — different story, must not leak.",
            },
        ],
        "story_timeline": [
            {
                "timeline_story_id": "s1",
                "timeline_event_index": 1,
                "timeline_when_label": "10:30",
                "timeline_what_text": "Oil futures spiked.",
            },
            {
                "timeline_story_id": "s1",
                "timeline_event_index": 0,
                "timeline_when_label": "08:10",
                "timeline_what_text": "Parliament voted to consider closure.",
            },
            {
                "timeline_story_id": "s2",
                "timeline_event_index": 0,
                "timeline_when_label": "Mon",
                "timeline_what_text": "S2 DECOY event.",
            },
        ],
        "caption_sentences": [
            {
                "caption_story_id": "s1",
                "sentence_index": 1,
                "sentence_text": "It carries a fifth of the world's oil.",
            },
            {
                "caption_story_id": "s1",
                "sentence_index": 0,
                "sentence_text": "Tensions rose over the Strait of Hormuz today.",
            },
        ],
        "story_sources": [
            {
                "source_story_id": "s1",
                "source_outlet_name": "Reuters",
                "source_article_url": "https://reuters.com/world/hormuz",
                "source_bias_lean": "center",
                "source_is_citation": True,
            },
            {
                "source_story_id": "s1",
                "source_outlet_name": "CNN",
                "source_article_url": None,
                "source_bias_lean": "left",
                "source_is_citation": True,
            },
            {
                "source_story_id": "s2",
                "source_outlet_name": "DECOY-OUTLET",
                "source_article_url": "https://decoy.example",
                "source_bias_lean": "right",
                "source_is_citation": True,
            },
        ],
    }


@pytest.fixture
def seed_tables() -> dict[str, list[dict[str, Any]]]:
    """The two-story seed dict (mutable per-test for edge-case fixtures)."""
    return _make_seed_tables()


@pytest.fixture
def fake_supabase_client(
    seed_tables: dict[str, list[dict[str, Any]]],
) -> FakeSupabaseClient:
    """A boundary-mocked Supabase client seeded with s1 (+ an s2 decoy)."""
    return FakeSupabaseClient(seed_tables)


@pytest.fixture
def s1_corpus(fake_supabase_client: FakeSupabaseClient) -> Any:
    """The assembled s1 :class:`GroundingCorpus`, loaded via SP1's real loader.

    Built from the same s1 seed the SP1 tests use, so the SP2 answerer/verifier
    tests ground against the SAME passages + citation targets the loader emits —
    proving citation provenance traces to s1's ``story_sources`` (Rule 9).
    """
    from agents.qa.corpus import load_grounding_corpus

    return load_grounding_corpus("s1", fake_supabase_client)


@pytest.fixture(autouse=True)
def _reset_corpus_cache() -> Any:
    """Clear the worker's process-local corpus cache between tests (hygiene)."""
    from agents.worker.corpus_cache import clear_corpus_cache

    clear_corpus_cache()
    yield
    clear_corpus_cache()
