"""Offline tests for the comma-anchor query backfill (issue #69).

Rule 9 — every test encodes WHY the behavior matters:
  - The founder's 26 hand-fixed queries (recovered verbatim from prod 2026-07-25) each
    derive >= 2 anchors with >= 1 strong one, and are LEFT ALONE by the backfill. WHY:
    they are the worked example of the contract — the shape the catalog is being moved
    to. If one of them ever stops deriving a strong anchor, the lexical key regressed
    and his feed goes empty again (exactly the 0-row run this slice exists to fix).
  - Every curated table value is validated the same way. WHY: writing a value that the
    key would weaken or drop is a silent re-run of the original bug on 77 more rows.
  - A row that needs a backfill but has no curated entry FAILS the whole plan. WHY: a
    guessed query is untraceable and unreviewable — deterministic curation or nothing
    (Rule 5 / Rule 12).
  - The UPDATE is guarded on the exact old value. WHY: it makes a re-run a no-op and
    makes a row edited between plan and write un-clobberable.

All I/O is faked at its boundary (an in-memory FakeConn) — NO network, NO prod, NO
model calls. The prod dry-run preview is the founder-gated acceptance step.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agents.ingestion.interest_lexical import derive_anchor_specs
from scripts.seed_catalog import backfill_anchor_queries as bf

# The founder's 26 followed interests, read back from prod after the 2026-07-25 hand
# fix. This IS the regression fixture: the contract's worked example, frozen.
ASH_FIXED_QUERIES: dict[str, str] = {
    "ai.alignment-research": "alignment research, superalignment, artificial intelligence safety, Anthropic, OpenAI, DeepMind",
    "ai.catastrophic-risk": "existential risk, catastrophic risk, artificial general intelligence, artificial intelligence risk",
    "ai.compute-energy-demand": "data center, data centers, electricity demand, power demand, hyperscaler, GPU cluster",
    "ai.data-center-buildout": "data center buildout, data center construction, data center campus, hyperscale data center, Stargate, CoreWeave",
    "ai.evals-red-teaming": "red teaming, model evaluation, LLM benchmark, LLM evals, jailbreak",
    "ai.interpretability": "mechanistic interpretability, interpretability, sparse autoencoder, model internals",
    "arts.ai-workforce": "job automation, workforce automation, artificial intelligence jobs, layoffs",
    "arts.bollywood": "Bollywood, Hindi cinema, Hindi film, Shah Rukh Khan",
    "arts.box-office": "box office, opening weekend, ticket sales",
    "business.gdp-growth": "GDP growth, economic growth, GDP",
    "business.inflation": "inflation, consumer prices, CPI, consumer price index",
    "business.interest-rates-fed": "Federal Reserve, interest rates, rate cut, rate hike, Jerome Powell, FOMC",
    "business.jobs": "unemployment, nonfarm payrolls, jobs, hiring, layoffs",
    "business.recession-risk": "recession, economic slowdown, economic downturn, soft landing",
    "crypto": "cryptocurrency, bitcoin, ethereum, blockchain, crypto, stablecoin",
    "entertainment": "Hollywood, celebrity, Netflix, film festival, television series, streaming",
    "geopolitics.critical-minerals": "critical minerals, rare earth, rare earths, lithium, cobalt",
    "geopolitics.natural-gas-pipelines": "natural gas, gas pipeline, LNG, pipeline",
    "geopolitics.oil-opec": "OPEC, oil prices, crude oil, oil production, Brent crude",
    "sport": "football, basketball, tennis, cricket, Olympics, championship, tournament",
    "sport.cricket": "cricket, test match, T20, IPL, ODI",
    "sport.cricket.india": "India cricket, Indian cricket, BCCI, Virat Kohli, Rohit Sharma",
    "sport.fifa": "FIFA, Gianni Infantino, football federation",
    "sport.fifa-world-cup": "FIFA World Cup, World Cup, World Cup final, World Cup qualifiers",
    "sport.soccer": "soccer, Premier League, Champions League, La Liga, Bundesliga",
    "tech.launches-missions": "rocket launch, SpaceX, NASA, space mission, Starship, ISRO",
}


class FakeConn:
    """In-memory stand-in for the asyncpg connection (records every statement)."""

    def __init__(self, rows: list[dict[str, Any]], changed_slugs: set[str] | None = None) -> None:
        self.rows = rows
        # Rows the UPDATE reports as changed; anything else returns "UPDATE 0" — the
        # shape a guard-value mismatch (or an already-applied re-run) produces.
        self.changed_slugs = {row["interest_slug"] for row in rows} if changed_slugs is None else changed_slugs
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return list(self.rows)

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "UPDATE 1" if args[0] in self.changed_slugs else "UPDATE 0"

    def transaction(self) -> Any:
        conn = self

        class _Txn:
            async def __aenter__(self) -> Any:
                return conn

            async def __aexit__(self, *_exc: Any) -> bool:
                return False

        return _Txn()


class TestAshTwentySixRegression:
    """The founder's hand-fixed queries are the contract's worked example."""

    @pytest.mark.parametrize("slug,query", sorted(ASH_FIXED_QUERIES.items()))
    def test_each_derives_two_anchors_with_one_strong(self, slug: str, query: str) -> None:
        """WHY: >= 2 anchors gives the interest more than one way in; >= 1 strong anchor
        is what lets it match on the story body/entities at all. A query failing either
        is the 2026-07-25 zero-row failure returning."""
        derivation = derive_anchor_specs(query)
        assert len(derivation.specs) >= 2, f"{slug}: {[s.anchor_phrase for s in derivation.specs]}"
        assert any(not spec.requires_title for spec in derivation.specs), slug
        assert derivation.banned_anchors == [], slug

    @pytest.mark.parametrize("slug,query", sorted(ASH_FIXED_QUERIES.items()))
    def test_backfill_leaves_them_alone(self, slug: str, query: str) -> None:
        """WHY: these 26 are already correct and were curated by hand. A backfill that
        re-wrote them would silently discard the founder's own fix."""
        assert bf.backfill_reason(query) is None, slug

    def test_fixture_count_is_the_full_followed_set(self) -> None:
        """WHY: a fixture that quietly shrinks stops being a regression net."""
        assert len(ASH_FIXED_QUERIES) == 26


class TestCuratedTable:
    """Every curated value must survive the lexical key it is written for."""

    @pytest.mark.parametrize("slug", sorted(bf.ANCHOR_QUERY_BACKFILL))
    def test_entry_validates(self, slug: str) -> None:
        """WHY: an entry that derives one anchor, no strong anchor, or a banned generic
        would be the same silent-no-match bug written to 77 more rows."""
        phrases = bf.validate_query(slug, bf.ANCHOR_QUERY_BACKFILL[slug])
        assert len(phrases) >= bf._MIN_ANCHORS

    def test_acronym_interest_keeps_the_acronym_and_gains_a_strong_anchor(self) -> None:
        """WHY (edge): 'NBA' is the whole interest — the fix is to ADD a strong
        companion anchor, never to drop the acronym. The acronym stays title-gated
        (a story is only about the NBA if the title says so); the companion is what
        lets the interest match on the entity haystack."""
        derivation = derive_anchor_specs(bf.ANCHOR_QUERY_BACKFILL["sport.nba"])
        by_phrase = {spec.anchor_phrase: spec.requires_title for spec in derivation.specs}
        assert by_phrase["nba"] is True
        assert by_phrase["basketball"] is False


class TestBuildPlan:
    """Planning is pure: it decides what changes and refuses to guess."""

    def test_soup_row_is_planned_with_before_and_after(self) -> None:
        """WHY (happy path): the soup shape is the whole bug — it must be detected from
        the stored value alone and paired with its curated replacement."""
        rows = [
            {
                "interest_slug": "ai.humanoid-robots",
                "interest_label": "Humanoid robots",
                "interest_search_query": "humanoid robots robotics news",
            }
        ]
        (change,) = bf.build_plan(rows)
        assert change.reason == "soup_query"
        assert change.old_query == "humanoid robots robotics news"
        assert change.new_query == bf.ANCHOR_QUERY_BACKFILL["ai.humanoid-robots"]
        assert "humanoid robots" in change.anchor_phrases

    def test_conforming_row_is_not_planned(self) -> None:
        """WHY: the backfill is scoped to broken rows — touching a healthy one risks
        undoing a better hand-curated query."""
        rows = [
            {
                "interest_slug": "ai.compute-energy-demand",
                "interest_label": "Compute & energy demand",
                "interest_search_query": ASH_FIXED_QUERIES["ai.compute-energy-demand"],
            }
        ]
        assert bf.build_plan(rows) == []

    def test_already_applied_row_is_not_replanned(self) -> None:
        """WHY (idempotency): re-running after a successful apply must plan zero rows,
        not re-write the same value."""
        rows = [
            {
                "interest_slug": "environment.hurricanes",
                "interest_label": "Hurricanes",
                "interest_search_query": bf.ANCHOR_QUERY_BACKFILL["environment.hurricanes"],
            }
        ]
        assert bf.build_plan(rows) == []

    def test_unmapped_broken_row_fails_the_whole_plan(self) -> None:
        """WHY (failure case): a broken row with no curated entry must abort the run —
        a guessed query is untraceable, and silently skipping it would leave an
        unmatchable interest in prod while reporting success (Rule 12)."""
        rows = [
            {
                "interest_slug": "brand.new.subniche",
                "interest_label": "Brand new subniche",
                "interest_search_query": "some brand new keyword soup news",
            }
        ]
        with pytest.raises(ValueError) as excinfo:
            bf.build_plan(rows)
        assert "brand.new.subniche" in str(excinfo.value)
        assert "fix_suggestion" in str(excinfo.value)

    def test_new_terms_annotation_flags_curated_additions(self) -> None:
        """WHY: the founder reviews this dry run. Terms that appear in neither the old
        query nor the label are where curation went beyond re-segmentation — the part
        that needs human eyes, so it must be called out rather than buried in the diff."""
        rows = [
            {
                "interest_slug": "sport.nba",
                "interest_label": "NBA",
                "interest_search_query": "NBA",
            }
        ]
        (change,) = bf.build_plan(rows)
        assert change.reason == "no_strong_anchor"
        assert "basketball" in change.new_terms


class TestValidateQueryFailsLoud:
    """A bad curated value must never reach prod."""

    def test_soup_replacement_is_rejected(self) -> None:
        """WHY: replacing soup with more soup is the bug wearing a new hat."""
        with pytest.raises(ValueError, match="soup"):
            bf.validate_query("x", "humanoid robots robotics hardware")

    def test_single_anchor_replacement_is_rejected(self) -> None:
        """WHY: one anchor gives the interest a single way in — the thin-query shape
        this backfill is meant to move away from."""
        with pytest.raises(ValueError, match="anchor"):
            bf.validate_query("x", "photography")

    def test_banned_standalone_replacement_is_rejected(self) -> None:
        """WHY: the key drops these silently, so a table entry built from them would
        look fine in review and match nothing in production."""
        with pytest.raises(ValueError, match="banned"):
            bf.validate_query("x", "foundation, trust")

    def test_phrase_losing_an_interior_word_is_rejected(self) -> None:
        """WHY: 'tariffs on China' looks like a perfect anchor and validates on every
        other rule — but the key drops 'on' and then joins what's left with \\s+, so the
        regex demands 'tariffs china' ADJACENT and never matches the headline it was
        written for. Caught in review of this very table; a curated query is only as
        good as the regex it compiles to."""
        with pytest.raises(ValueError, match="interior"):
            bf.validate_query("x", "China tariffs, tariffs on China")

    def test_trailing_dropped_word_is_allowed(self) -> None:
        """WHY (boundary): a word dropped at the END costs nothing — 'Arsenal FC'
        still yields the usable 'arsenal' anchor. Rejecting it would be a false alarm."""
        assert bf.validate_query("x", "Arsenal FC, Premier League") == [
            "arsenal",
            "premier league",
        ]

    def test_title_only_replacement_is_rejected(self) -> None:
        """WHY: all-acronym anchors can only match in the title — the interest still
        cannot match on the story body/entities."""
        with pytest.raises(ValueError, match="strong"):
            bf.validate_query("x", "GDP, CPI")


class TestApplyIsGuardedAndIdempotent:
    """The write path cannot clobber and cannot double-write."""

    def _plan(self) -> list[bf.PlannedQueryChange]:
        return bf.build_plan(
            [
                {
                    "interest_slug": "environment.hurricanes",
                    "interest_label": "Hurricanes",
                    "interest_search_query": "hurricane tropical storm news",
                }
            ]
        )

    def test_update_is_guarded_on_the_exact_old_value(self) -> None:
        """WHY: scoping by slug alone would overwrite whatever the row holds NOW,
        including a better value written after the plan was computed."""
        planned = self._plan()
        conn = FakeConn(rows=[])
        changed = asyncio.run(bf.apply_plan(conn, planned))
        sql, args = conn.executed[0]
        assert "interest_search_query = $3" in sql
        assert args == (
            "environment.hurricanes",
            bf.ANCHOR_QUERY_BACKFILL["environment.hurricanes"],
            "hurricane tropical storm news",
        )
        assert changed == 0  # guard did not match this FakeConn's changed set

    def test_rows_changed_counts_only_real_updates(self) -> None:
        """WHY: reporting planned-count as applied-count would hide a guard mismatch —
        'applied 77' when the DB moved zero rows (Rule 12)."""
        planned = self._plan()
        conn = FakeConn(rows=[], changed_slugs={"environment.hurricanes"})
        assert asyncio.run(bf.apply_plan(conn, planned)) == 1


class TestSnapshotIsTheRollbackManifest:
    """No snapshot, no rollback — so the snapshot must carry the OLD values."""

    def test_snapshot_carries_old_values_for_every_planned_row(self, tmp_path: Any) -> None:
        """WHY: the snapshot is the only way back after the write. It must hold the
        verbatim pre-write value for each row, keyed by the slug the restore targets."""
        planned = bf.build_plan(
            [
                {
                    "interest_slug": "environment.hurricanes",
                    "interest_label": "Hurricanes",
                    "interest_search_query": "hurricane tropical storm news",
                }
            ]
        )
        path = str(tmp_path / "snap.json")
        bf.write_snapshot(path, planned)
        payload = json.loads(open(path, encoding="utf-8").read())
        assert payload["issue"] == 69
        assert payload["row_count"] == 1
        assert payload["rows"][0]["interest_slug"] == "environment.hurricanes"
        assert payload["rows"][0]["old_query"] == "hurricane tropical storm news"


class TestDryRunReport:
    """The dry-run report is the founder-gate artifact."""

    def test_report_shows_before_after_and_counts(self) -> None:
        """WHY: the founder approves the APPLY from this text alone, so it has to show
        what changes, to what, and how many rows are in scope."""
        planned = bf.build_plan(
            [
                {
                    "interest_slug": "environment.hurricanes",
                    "interest_label": "Hurricanes",
                    "interest_search_query": "hurricane tropical storm news",
                }
            ]
        )
        report = bf.format_dry_run_report(planned, scanned=237)
        assert "interests scanned: 237" in report
        assert "would update: 1" in report
        assert "before: hurricane tropical storm news" in report
        assert f"after : {bf.ANCHOR_QUERY_BACKFILL['environment.hurricanes']}" in report
