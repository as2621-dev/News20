"""Offline tests for the LLM candidate generator (Phase 5f SP1).

DoD (Rule 9 — tests encode WHY, not just WHAT):
  - A well-formed model response is parsed and written as valid candidates. WHY:
    the seeder reads these files verbatim; a generator that mangled the schema
    would feed the resolvers garbage and leave every deck empty.
  - Malformed / garbage model output is dropped with a logged count, NOT a crash.
    WHY: this runs across ~48 cells in SP2/SP3; one bad cell must never abort the
    batch (Rule 12 — fail loud per cell, not the whole run).
  - A candidate whose ``topic_tags[0]`` is not one of the 8 keys is coerced (when
    a valid key appears later) or dropped (when none does). WHY: the SourceSwipe
    decks filter on the 8-key axis; an off-axis tag makes a source invisible or
    mis-placed in onboarding.
  - The same handle proposed twice collapses to one row. WHY: the seeder upserts
    on the axis identity key; duplicate proposals would inflate the cell's count
    past its real distinct-source coverage and waste resolver quota.

The LLM is mocked at the ``LLMClient.call_gemini`` boundary — no live Gemini call,
no key, no cost (CLAUDE.md mocking mandate). No YouTube/network is touched (the
generator only proposes; resolution is the seeder's job, tested separately).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from scripts.seed_catalog import generate_candidates
from scripts.seed_catalog.seed_catalog import ALLOWED_TOPIC_TAGS


# ── Helpers ────────────────────────────────────────────────────────────────────


def _mock_client(model_text: str) -> Any:
    """Build a stand-in LLMClient whose ``call_gemini`` returns canned text.

    Args:
        model_text: The exact string the mocked model "returns".

    Returns:
        An object with an async ``call_gemini`` returning ``model_text``.
    """

    class _MockLLMClient:
        def __init__(self) -> None:
            self.call_gemini = AsyncMock(return_value=model_text)

    return _MockLLMClient()


def _run_cell(
    *,
    entry_type: str,
    archetype: str,
    model_text: str,
    tmp_path: Path,
    count: int = 75,
) -> list[dict[str, Any]]:
    """Run ``generate_cell`` with a mocked LLM against a temp data dir.

    Args:
        entry_type: The axis to generate.
        archetype: The archetype slug.
        model_text: The canned model response.
        tmp_path: A pytest tmp dir used as the data directory.
        count: The per-cell cap.

    Returns:
        The final candidate list (also written into ``tmp_path``).
    """
    return asyncio.run(
        generate_candidates.generate_cell(
            entry_type,
            archetype,
            count=count,
            llm_client=_mock_client(model_text),
            data_dir=tmp_path,
        )
    )


# ── Happy path ─────────────────────────────────────────────────────────────────


def test_well_formed_model_json_is_parsed_and_written(tmp_path: Path) -> None:
    """A clean JSON array → valid candidates written to the cell file.

    WHY: the seeder consumes ``data/{type}.{archetype}.json`` verbatim, so the
    generator MUST emit the exact per-axis schema (here: ``youtube_handle`` +
    ``topic_tags``) for the resolver to have anything real to resolve.
    """
    model_text = json.dumps(
        [
            {"youtube_handle": "lexfridman", "topic_tags": ["ai", "tech"]},
            {"youtube_handle": "AndrejKarpathy", "topic_tags": ["ai"]},
        ]
    )
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )

    assert [c["youtube_handle"] for c in final] == ["lexfridman", "AndrejKarpathy"]
    assert final[0]["topic_tags"][0] in ALLOWED_TOPIC_TAGS

    written = json.loads((tmp_path / "channels.ai-frontier-tech.json").read_text())
    assert written == final


def test_fenced_json_block_is_unwrapped(tmp_path: Path) -> None:
    """A ```json fenced response still parses.

    WHY: chat models routinely wrap arrays in a markdown fence despite a
    "no code fences" instruction; the generator must tolerate that or it would
    drop otherwise-valid cells to zero.
    """
    model_text = (
        "```json\n"
        + json.dumps(
            [{"handle": "@karpathy", "source_name": "AK", "topic_tags": ["ai"]}]
        )
        + "\n```"
    )
    final = _run_cell(
        entry_type="x",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    assert final == [{"handle": "@karpathy", "source_name": "AK", "topic_tags": ["ai"]}]


# ── Failure case ───────────────────────────────────────────────────────────────


def test_garbage_model_output_yields_empty_cell_without_crashing(
    tmp_path: Path,
) -> None:
    """Non-JSON prose → empty list, file written empty, NO exception.

    WHY: SP2/SP3 sweep ~48 cells; a single model refusal or hallucinated prose
    must degrade to an empty cell (logged), never abort the batch (Rule 12).
    """
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text="I'm sorry, I can't help with that request.",
        tmp_path=tmp_path,
    )
    assert final == []
    assert json.loads((tmp_path / "channels.ai-frontier-tech.json").read_text()) == []


def test_parse_candidate_array_returns_empty_on_non_array_json() -> None:
    """A JSON object (not an array) parses to an empty list, not a crash.

    WHY: the seeder requires a top-level array; a model returning ``{...}`` must
    be treated as a miss, not silently coerced into one malformed entry.
    """
    assert generate_candidates.parse_candidate_array('{"oops": true}') == []


# ── Edge: topic-tag validation ─────────────────────────────────────────────────


def test_candidate_with_invalid_first_tag_is_coerced_to_valid_key(
    tmp_path: Path,
) -> None:
    """``topic_tags[0]`` not in the 8 keys but a valid key later → promoted, kept.

    WHY: the deck filters on the 8-key axis; rescuing a candidate that merely
    mis-ordered its tags (vs. dropping it) preserves real coverage while still
    guaranteeing position 0 is a valid key.
    """
    model_text = json.dumps(
        [{"youtube_handle": "x", "topic_tags": ["machine-learning", "ai"]}]
    )
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    assert final == [{"youtube_handle": "x", "topic_tags": ["ai"]}]


def test_candidate_with_no_valid_topic_tag_is_dropped(tmp_path: Path) -> None:
    """A candidate whose every tag is off-axis is dropped (no rescue possible).

    WHY: a source with no valid 8-key tag can never surface on any deck — keeping
    it would write an invisible row and overstate the cell's coverage.
    """
    model_text = json.dumps(
        [
            {"youtube_handle": "keepme", "topic_tags": ["ai"]},
            {"youtube_handle": "dropme", "topic_tags": ["nonsense", "alsobad"]},
        ]
    )
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    assert [c["youtube_handle"] for c in final] == ["keepme"]


def test_candidate_missing_identity_field_is_dropped(tmp_path: Path) -> None:
    """A candidate with no identity field (here: no ``youtube_handle``) is dropped.

    WHY: the seeder's dedup + upsert keys on the identity field; an entry without
    one has no stable external id and cannot be resolved or de-duplicated.
    """
    model_text = json.dumps(
        [
            {"youtube_handle": "keepme", "topic_tags": ["ai"]},
            {"topic_tags": ["ai"]},
        ]
    )
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    assert [c["youtube_handle"] for c in final] == ["keepme"]


# ── Edge: dedup ────────────────────────────────────────────────────────────────


def test_duplicate_handle_collapses_to_one(tmp_path: Path) -> None:
    """The same handle proposed twice (case-insensitive) collapses to one entry.

    WHY: the seeder upserts on the lowercased identity; un-deduped proposals would
    inflate the cell's apparent coverage and waste a resolver call on a duplicate.
    """
    model_text = json.dumps(
        [
            {"youtube_handle": "LexFridman", "topic_tags": ["ai"]},
            {"youtube_handle": "lexfridman", "topic_tags": ["tech"]},
            {"youtube_handle": "AndrejKarpathy", "topic_tags": ["ai"]},
        ]
    )
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    handles_lower = [c["youtube_handle"].lower() for c in final]
    assert handles_lower == ["lexfridman", "andrejkarpathy"]


def test_x_handle_dedup_ignores_leading_at(tmp_path: Path) -> None:
    """``@karpathy`` and ``karpathy`` collapse — the @ is not part of the identity.

    WHY: the seeder strips the leading @ before keying; the generator must dedup
    on the SAME normalised identity or a "@"-prefixed dupe would slip through.
    """
    model_text = json.dumps(
        [
            {"handle": "@karpathy", "source_name": "AK", "topic_tags": ["ai"]},
            {"handle": "karpathy", "source_name": "AK2", "topic_tags": ["tech"]},
        ]
    )
    final = _run_cell(
        entry_type="x",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    assert len(final) == 1


# ── Existing-file union (curated picks preserved) ──────────────────────────────


def test_existing_curated_entries_are_preserved_and_ranked_first(
    tmp_path: Path,
) -> None:
    """Curated entries already in the file survive a regenerate and stay best-first.

    WHY: re-running the generator must not blow away a curator's hand-picked
    sources; they are unioned in at the top so their popularity rank is retained.
    """
    target = tmp_path / "channels.ai-frontier-tech.json"
    target.write_text(
        json.dumps([{"youtube_handle": "curatedpick", "topic_tags": ["ai"]}])
    )
    model_text = json.dumps([{"youtube_handle": "modelpick", "topic_tags": ["ai"]}])
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
    )
    assert [c["youtube_handle"] for c in final] == ["curatedpick", "modelpick"]


def test_count_caps_the_final_list(tmp_path: Path) -> None:
    """The final list is capped at ``count`` even when more valid candidates exist.

    WHY: ``--count`` is the over-generation budget; honoring it keeps the cell
    size predictable for the SP2 quota math (calls ≈ cell size).
    """
    model_text = json.dumps(
        [{"youtube_handle": f"chan{i}", "topic_tags": ["ai"]} for i in range(10)]
    )
    final = _run_cell(
        entry_type="channels",
        archetype="ai-frontier-tech",
        model_text=model_text,
        tmp_path=tmp_path,
        count=4,
    )
    assert len(final) == 4
