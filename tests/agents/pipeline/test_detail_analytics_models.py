"""Unit tests for the Phase 2c Detail-analytics Pydantic models.

DoD (phase file SP2 / SP1 — the validation half):
  - A valid ``story_analytics`` payload (a ``SecondAnalytic`` with ``AnalyticRow``s)
    round-trips through the models and serializes to the JSONB shape Postgres stores.
  - A MALFORMED ``analytic_rows`` element is REJECTED before it can reach the DB.

These tests encode WHY the validation matters (Rule 9): ``story_analytics.analytic_rows``
is JSONB, so without per-element model validation a fabricated/malformed row would be
written raw to Postgres (CLAUDE.md §0 — never raw dicts at the boundary). The malformed
cases assert a ``ValidationError`` is raised, so deleting the ``AnalyticRow`` validation
(or loosening it to ``dict``) breaks the suite — it cannot silently pass.

    >>> pytest tests/agents/pipeline/test_detail_analytics_models.py -v
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.pipeline.models import (
    AnalyticRow,
    CoverageReport,
    DetailKeyPoint,
    SecondAnalytic,
)


# ── Happy path: a valid story_analytics payload round-trips ───────────────────


def test_second_analytic_with_grounded_rows_round_trips() -> None:
    """A valid market_impact analytic + 2 grounded rows survives validation and
    serializes to the JSONB array shape Postgres stores in ``analytic_rows``."""
    analytic = SecondAnalytic(
        analytic_story_id="s1",
        analytic_kind="market_impact",
        analytic_tab_label="MARKET IMPACT",
        analytic_headline="Oil markets twitch on the Hormuz threat",
        analytic_summary_text="A closure would choke ~20% of seaborne crude.",
        analytic_rows=[
            AnalyticRow(
                analytic_row_label="Brent crude",
                analytic_row_value="+4%",
                analytic_row_direction="up",
            ),
            AnalyticRow(
                analytic_row_label="EUR/USD",
                analytic_row_value=None,
                analytic_row_direction="down",
                analytic_row_note="direction-only; no source figure",
            ),
        ],
        analytic_is_grounded=True,
    )

    assert analytic.analytic_kind == "market_impact"
    assert len(analytic.analytic_rows) == 2
    assert analytic.analytic_is_grounded is True

    # The JSONB array shape persisted to story_analytics.analytic_rows.
    rows_jsonb = [row.model_dump() for row in analytic.analytic_rows]
    assert rows_jsonb[0] == {
        "analytic_row_label": "Brent crude",
        "analytic_row_value": "+4%",
        "analytic_row_direction": "up",
        "analytic_row_note": None,
    }
    # direction-only fallback: value is None but the row is still valid.
    assert rows_jsonb[1]["analytic_row_value"] is None
    assert rows_jsonb[1]["analytic_row_direction"] == "down"


def test_analytic_rows_validated_from_raw_dicts_before_insert() -> None:
    """The persist boundary receives raw dicts (from the LLM stage); each MUST be
    validated through AnalyticRow. A well-formed dict list validates cleanly."""
    raw_rows = [
        {
            "analytic_row_label": "S&P 500",
            "analytic_row_value": "-1.2%",
            "analytic_row_direction": "down",
        },
        {
            "analytic_row_label": "Gold",
            "analytic_row_value": "$2,400",
            "analytic_row_direction": "up",
        },
    ]
    validated = [AnalyticRow.model_validate(row) for row in raw_rows]
    assert [row.analytic_row_label for row in validated] == ["S&P 500", "Gold"]


# ── Failure case: a malformed analytic_rows element is REJECTED (Rule 9) ───────


def test_malformed_analytic_row_missing_label_is_rejected() -> None:
    """A row with no ``analytic_row_label`` must not reach Postgres — the required
    field forces a ValidationError. (Drop the model and this can't fail.)"""
    with pytest.raises(ValidationError):
        AnalyticRow.model_validate(
            {"analytic_row_value": "+4%", "analytic_row_direction": "up"}
        )


def test_malformed_analytic_row_bad_direction_is_rejected() -> None:
    """An out-of-enum direction ("skyrocket") must be rejected — only
    up/down/flat/None are legal glyphs."""
    with pytest.raises(ValidationError):
        AnalyticRow.model_validate(
            {"analytic_row_label": "Brent crude", "analytic_row_direction": "skyrocket"}
        )


def test_malformed_analytic_row_unknown_field_is_rejected() -> None:
    """``extra='forbid'`` on AnalyticRow rejects unexpected keys, so a hallucinated
    extra field (e.g. a fabricated 'confidence') cannot smuggle into the JSONB."""
    with pytest.raises(ValidationError):
        AnalyticRow.model_validate(
            {
                "analytic_row_label": "Brent crude",
                "analytic_row_value": "+4%",
                "fabricated_confidence": 0.99,
            }
        )


def test_second_analytic_rejects_malformed_row_in_array() -> None:
    """A malformed element anywhere in ``analytic_rows`` fails the whole
    SecondAnalytic — the array cannot be partially-valid (the persist gate is
    all-or-nothing)."""
    with pytest.raises(ValidationError):
        SecondAnalytic.model_validate(
            {
                "analytic_story_id": "s1",
                "analytic_kind": "ripple",
                "analytic_tab_label": "RIPPLE",
                "analytic_headline": "h",
                "analytic_summary_text": "s",
                "analytic_rows": [
                    {"analytic_row_label": "ok row", "analytic_row_value": "+1%"},
                    {
                        "analytic_row_value": "+4%"
                    },  # missing required label → reject all
                ],
                "analytic_is_grounded": False,
            }
        )


def test_second_analytic_rejects_out_of_enum_kind() -> None:
    """``analytic_kind`` is a closed enum — a wrong-segment kind ('weather') must be
    rejected so a mis-mapped tab can never persist."""
    with pytest.raises(ValidationError):
        SecondAnalytic.model_validate(
            {
                "analytic_story_id": "s1",
                "analytic_kind": "weather",
                "analytic_tab_label": "WEATHER",
                "analytic_headline": "h",
                "analytic_summary_text": "s",
                "analytic_rows": [],
                "analytic_is_grounded": False,
            }
        )


# ── Edge cases: empty rows + the reach/partisan CoverageReport shapes ──────────


def test_second_analytic_allows_zero_rows() -> None:
    """A story whose every numeric value was dropped (Decision #5) may publish an
    analytic with zero rows — the narrative still renders, just no figure table."""
    analytic = SecondAnalytic(
        analytic_story_id="s4",
        analytic_kind="stakes",
        analytic_tab_label="STAKES",
        analytic_headline="What's on the line",
        analytic_summary_text="A loss ends their season.",
        analytic_rows=[],
        analytic_is_grounded=False,
    )
    assert analytic.analytic_rows == []


def test_coverage_report_reach_mode_caps_notable_outlets_at_five() -> None:
    """Reach mode shows at most 5 notable outlets — a 6th must be rejected so the
    UI strip never overflows (the schema column is sized for 5)."""
    with pytest.raises(ValidationError):
        CoverageReport(
            coverage_mode="reach",
            coverage_outlet_count=30,
            coverage_momentum="developing",
            coverage_originating_outlet_name="Reuters",
            coverage_notable_outlet_names=[
                "Reuters",
                "BBC News",
                "AP",
                "Bloomberg",
                "Al Jazeera",
                "CNN",
            ],
        )


def test_coverage_report_partisan_mode_defaults_reach_fields_none() -> None:
    """Partisan mode carries L/C/R counts + blindspot; the reach-only fields stay
    None/empty so a partisan row never leaks a stray momentum/originating value."""
    report = CoverageReport(
        coverage_mode="partisan",
        coverage_left_count=8,
        coverage_center_count=10,
        coverage_right_count=2,
        coverage_outlet_count=20,
        blindspot_lean="right",
    )
    assert report.coverage_momentum is None
    assert report.coverage_originating_outlet_name is None
    assert report.coverage_notable_outlet_names == []
    assert report.blindspot_lean == "right"


def test_detail_key_point_requires_nonempty_text() -> None:
    """A blank bullet must be rejected — an empty key point would render as a stray
    red dot with no text."""
    with pytest.raises(ValidationError):
        DetailKeyPoint(key_point_index=0, key_point_text="")
