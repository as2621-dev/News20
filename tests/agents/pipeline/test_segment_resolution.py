"""Honest segment tagging: every persisted story carries one canonical 8-root segment.

WHY these tests exist (Rule 9): the prod 07-07 batch persisted **0 of 67** stories
under ai/business/arts because ``_VALID_SEGMENT_SLUGS`` was still the pre-taxonomy
5-set ``{geopolitics, markets, tech, sport, wildcard}`` and every unmatched story
silently became ``wildcard``. A junk default masquerading as a classification is the
exact bug class here (compare ``docs/solutions/architecture-patterns/
no-signal-returns-none-not-default-plus-twin-drift-test.md``) — so the contract is
now "resolve a real root, or return ``None`` and reject loudly".

A test that passes while a ``wildcard`` default exists is wrong: these assert the
unresolvable case **rejects and logs**, never that it returns something.

Pure functions — no DB, no LLM, no clock.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from agents.ingestion.models import StoryInterestTag
from agents.pipeline.categories import TOPIC_CATEGORIES
from agents.pipeline.persist_helpers import (
    _VALID_SEGMENT_SLUGS,
    resolve_segment_from_tags,
)


def _tag(interest_id: str, depth: int = 0) -> StoryInterestTag:
    return StoryInterestTag(
        story_interest_story_id="story-1",
        story_interest_interest_id=interest_id,
        story_interest_match_depth=depth,
    )


def _event(captured: list[dict], event_name: str) -> dict | None:
    """Return the first captured structlog event with ``event_name``, else None."""
    return next((e for e in captured if e.get("event") == event_name), None)


class TestEightRootHappyPath:
    """Every one of the 8 canonical roots survives resolution unchanged."""

    @pytest.mark.parametrize("root", TOPIC_CATEGORIES)
    def test_each_root_persists_under_its_own_segment(self, root: str) -> None:
        """Table-driven over all 8 roots — fails if the stale 5-set returns.

        Under the 5-set, ai/business/environment/politics/arts all fell through to
        the ``wildcard`` default; this parametrization is what makes that regression
        impossible to reintroduce silently.
        """
        assert resolve_segment_from_tags([_tag("int-a")], {"int-a": root}) == root

    def test_the_valid_set_is_exactly_the_eight_roots(self) -> None:
        """The validation set IS the taxonomy — not a hand-copied set beside it.

        RC1 was a hand-written 5-set that nobody widened when the 8-root taxonomy
        landed. Asserting the identity (rather than just "markets isn't in it")
        is what fails if anyone re-literalizes this set.
        """
        assert _VALID_SEGMENT_SLUGS == set(TOPIC_CATEGORIES)
        assert not _VALID_SEGMENT_SLUGS & {"markets", "wildcard"}


class TestLowestDepthWins:
    """The closest (leaf) match characterizes the story; conflicts are logged."""

    def test_lowest_match_depth_tag_wins(self) -> None:
        tags = [_tag("int-sport", depth=2), _tag("int-ai", depth=0)]
        lookup = {"int-sport": "sport", "int-ai": "ai"}
        assert resolve_segment_from_tags(tags, lookup) == "ai"

    def test_conflicting_roots_are_logged_as_a_conflict(self) -> None:
        """Two tags resolving to different roots → depth-0 wins AND is logged.

        A silent pick would hide a mis-tagged story; the log is how a batch's
        conflict rate becomes visible.
        """
        tags = [_tag("int-sport", depth=2), _tag("int-ai", depth=0)]
        lookup = {"int-sport": "sport", "int-ai": "ai"}
        with capture_logs() as events:
            assert resolve_segment_from_tags(tags, lookup) == "ai"
        conflict = _event(events, "segment_resolution_conflict")
        assert conflict is not None
        assert conflict["chosen_segment"] == "ai"
        assert conflict["candidate_segments"] == ["ai", "sport"]

    def test_single_root_across_tags_logs_no_conflict(self) -> None:
        tags = [_tag("int-ai-leaf", depth=0), _tag("int-ai-root", depth=1)]
        lookup = {"int-ai-leaf": "ai", "int-ai-root": "ai"}
        with capture_logs() as events:
            assert resolve_segment_from_tags(tags, lookup) == "ai"
        assert _event(events, "segment_resolution_conflict") is None


class TestLegacySlugsNeverPropagate:
    """Legacy enum values from old data are mapped to a live root, never emitted."""

    @pytest.mark.parametrize(
        ("legacy_slug", "expected_root"),
        [("markets", "business"), ("wildcard", "arts"), ("world", "geopolitics")],
    )
    def test_legacy_slug_maps_to_its_live_root(
        self, legacy_slug: str, expected_root: str
    ) -> None:
        """``markets``/``wildcard`` arriving from old interests rows are folded.

        The Postgres enum retains them for reversibility, so they CAN arrive; the
        pipeline must never write one back out.
        """
        resolved = resolve_segment_from_tags([_tag("int-a")], {"int-a": legacy_slug})
        assert resolved == expected_root
        assert resolved in TOPIC_CATEGORIES

    def test_dotted_leaf_slug_resolves_to_its_root(self) -> None:
        """A lookup value that is a dotted interest slug folds to its root."""
        resolved = resolve_segment_from_tags(
            [_tag("int-a")], {"int-a": "business.equities.semis"}
        )
        assert resolved == "business"

    def test_unmappable_value_is_skipped_not_defaulted(self) -> None:
        """An unknown slug must NOT quietly become the arts catch-all.

        ``category_for_slug`` defaults unknown roots to arts — correct as the LAST
        resolver, poisonous here, where it would masquerade as a classification.
        """
        assert resolve_segment_from_tags([_tag("int-a")], {"int-a": "zzz-unknown"}) is None

    def test_a_resolvable_tag_still_wins_past_an_unmappable_one(self) -> None:
        tags = [_tag("int-junk", depth=0), _tag("int-ai", depth=1)]
        lookup = {"int-junk": "zzz-unknown", "int-ai": "ai"}
        assert resolve_segment_from_tags(tags, lookup) == "ai"


class TestUnresolvableIsRejectedNotDefaulted:
    """The whole point of the slice: no signal → ``None`` + a loud structured log."""

    @pytest.mark.parametrize("lookup", [None, {}])
    def test_empty_interest_lookup_rejects(self, lookup) -> None:
        assert resolve_segment_from_tags([_tag("int-a")], lookup) is None

    def test_no_tags_at_all_rejects(self) -> None:
        """Zero tags = zero interests to derive a root from → reject.

        The interest-keyed pipeline always stamps a depth-0 provenance tag, so an
        untagged story reaching here is itself the bug worth surfacing.
        """
        assert resolve_segment_from_tags([], {"int-a": "ai"}) is None

    def test_tag_absent_from_the_lookup_rejects(self) -> None:
        assert resolve_segment_from_tags([_tag("int-missing")], {"int-a": "ai"}) is None

    def test_rejection_logs_with_a_fix_suggestion(self) -> None:
        """CLAUDE.md mandates ``fix_suggestion`` on error logs — and it is the only
        way an operator learns WHY a story vanished from a batch."""
        with capture_logs() as events:
            assert resolve_segment_from_tags([_tag("int-a")], {}) is None
        failure = _event(events, "segment_resolution_failed")
        assert failure is not None
        assert failure["log_level"] == "error"
        assert failure["story_id"] == "story-1"
        assert failure["fix_suggestion"]
