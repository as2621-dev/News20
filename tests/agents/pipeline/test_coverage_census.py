"""Unit tests for the per-niche coverage census (slice #5).

No Supabase, no network: :func:`build_coverage_census` is pure over injected data, and
:func:`fetch_census_inputs` is exercised through a fake Supabase client mocked ONLY at
the ``.table(...).select(...)`` boundary.

WHY these matter (Rule 9 — encode intent):
  * The census measures the PRD's riskiest assumption (≥60% direct-niche hit rate).
    It MUST count node-tagged (direct) stories, not pool-wide stories — a test locks
    that a persona's count reflects only tags on its own interests.
  * A dry niche (zero hits) and a missing day (no batch run) are DIFFERENT findings —
    tests lock that a dry interest is still listed and a no-batch day is "missing",
    not "zero coverage".
  * The report shape is consumed by the M4 validation slice (#10) — a shape test with
    mocked data pins the field surface so a later change can't silently break #10.
  * Read-only-ness is a safety contract — a test asserts the fetch seam never calls a
    write method (insert/update/upsert/delete/rpc/storage) on the client.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agents.pipeline.coverage_census import (
    HIT_RATE_TARGET,
    CoverageCensusReport,
    PersonaProfile,
    SeededInterest,
    TagObservation,
    build_coverage_census,
    fetch_census_inputs,
    render_report_text,
    window_dates,
)


def _interest(
    interest_id: str,
    slug: str,
    *,
    depth: int = 3,
    added: str = "2026-07-01",
    ancestors: list[tuple[str, str, int]] | None = None,
) -> SeededInterest:
    ancestors = ancestors or []
    return SeededInterest(
        interest_id=interest_id,
        interest_slug=slug,
        interest_label=slug.split(".")[-1].upper(),
        interest_depth=depth,
        profile_added_date=added,
        ancestor_interest_ids=[a[0] for a in ancestors],
        ancestor_slugs=[a[1] for a in ancestors],
    )


class TestWindowDates:
    """The UTC-date window helper."""

    def test_inclusive_ascending_range(self) -> None:
        assert window_dates("2026-07-01", "2026-07-03") == [
            "2026-07-01",
            "2026-07-02",
            "2026-07-03",
        ]

    def test_single_day_window(self) -> None:
        assert window_dates("2026-07-03", "2026-07-03") == ["2026-07-03"]

    def test_start_after_end_is_empty(self) -> None:
        assert window_dates("2026-07-05", "2026-07-03") == []


class TestBuildCensusHappyPath:
    """Per-interest per-day direct counts + hit-rate summary vs the 60% target."""

    def test_counts_direct_tags_per_interest_per_day(self) -> None:
        persona = PersonaProfile(
            persona_key="cricket",
            persona_email="persona.cricket@news20.seed",
            persona_user_id="u1",
            interests=[
                _interest(
                    "ipl",
                    "sport.cricket.ipl",
                    ancestors=[("cricket", "sport.cricket", 2), ("sport", "sport", 0)],
                )
            ],
        )
        tags = [
            TagObservation(interest_id="ipl", story_id="s1", created_date="2026-07-01"),
            TagObservation(interest_id="ipl", story_id="s2", created_date="2026-07-01"),
            TagObservation(interest_id="ipl", story_id="s3", created_date="2026-07-02"),
            # Ancestor context (fallback material) — not counted as a direct ipl hit.
            TagObservation(
                interest_id="sport", story_id="s9", created_date="2026-07-01"
            ),
        ]
        report = build_coverage_census(
            personas=[persona],
            tag_observations=tags,
            present_days={"2026-07-01", "2026-07-02"},
            window_days=window_dates("2026-07-01", "2026-07-02"),
            generated_at_utc="2026-07-03T00:00:00+00:00",
        )
        cov = report.personas[0].interests[0]
        by_day = {d.census_date: d.direct_hit_count for d in cov.per_day}
        assert by_day == {"2026-07-01": 2, "2026-07-02": 1}
        assert cov.total_direct_hits == 3
        assert cov.hit_day_count == 2
        # Both eligible days hit ⇒ 100% for this persona.
        assert report.personas[0].hit_rate == 1.0
        assert report.personas[0].meets_target is True
        assert report.overall_meets_target is True

    def test_counts_only_node_tagged_not_pool_wide(self) -> None:
        # A tag on an UNRELATED interest must never inflate a persona's count.
        persona = PersonaProfile(
            persona_key="chip",
            persona_email="persona.chip@news20.seed",
            persona_user_id="u2",
            interests=[_interest("tsmc", "tech.semiconductors.tsmc")],
        )
        tags = [
            TagObservation(interest_id="tsmc", story_id="s1", created_date="2026-07-01"),
            TagObservation(
                interest_id="unrelated", story_id="s2", created_date="2026-07-01"
            ),
        ]
        report = build_coverage_census(
            personas=[persona],
            tag_observations=tags,
            present_days={"2026-07-01"},
            window_days=["2026-07-01"],
        )
        assert report.personas[0].interests[0].total_direct_hits == 1

    def test_ladder_context_shows_ancestor_totals(self) -> None:
        persona = PersonaProfile(
            persona_key="cricket",
            persona_email="persona.cricket@news20.seed",
            persona_user_id="u1",
            interests=[
                _interest(
                    "ipl",
                    "sport.cricket.ipl",
                    ancestors=[("cricket", "sport.cricket", 2), ("sport", "sport", 0)],
                )
            ],
        )
        tags = [
            TagObservation(interest_id="ipl", story_id="s1", created_date="2026-07-01"),
            TagObservation(
                interest_id="cricket", story_id="s2", created_date="2026-07-01"
            ),
            TagObservation(
                interest_id="sport", story_id="s3", created_date="2026-07-01"
            ),
            TagObservation(
                interest_id="sport", story_id="s4", created_date="2026-07-01"
            ),
        ]
        report = build_coverage_census(
            personas=[persona],
            tag_observations=tags,
            present_days={"2026-07-01"},
            window_days=["2026-07-01"],
        )
        ladder = {
            lvl.ladder_slug: lvl.total_direct_hits
            for lvl in report.personas[0].interests[0].ladder_context
        }
        assert ladder == {"sport.cricket.ipl": 1, "sport.cricket": 1, "sport": 2}


class TestBuildCensusEdgeCases:
    """Dry niches and missing days — the findings, not noise."""

    def test_zero_hit_interest_is_listed_not_dropped(self) -> None:
        persona = PersonaProfile(
            persona_key="founder",
            persona_email="persona.founder@news20.seed",
            persona_user_id="u3",
            interests=[_interest("dry", "business.startups.pre-seed")],
        )
        report = build_coverage_census(
            personas=[persona],
            tag_observations=[],
            present_days={"2026-07-01"},
            window_days=["2026-07-01"],
        )
        cov = report.personas[0].interests[0]
        assert cov.is_dry is True
        assert cov.total_direct_hits == 0
        assert report.personas[0].hit_rate == 0.0
        assert report.personas[0].meets_target is False

    def test_missing_day_reported_missing_not_zero(self) -> None:
        persona = PersonaProfile(
            persona_key="cricket",
            persona_email="persona.cricket@news20.seed",
            persona_user_id="u1",
            interests=[_interest("ipl", "sport.cricket.ipl")],
        )
        tags = [
            TagObservation(interest_id="ipl", story_id="s1", created_date="2026-07-01"),
        ]
        # Batch ran on the 1st + 3rd; the 2nd had NO batch run (not in present_days).
        report = build_coverage_census(
            personas=[persona],
            tag_observations=tags,
            present_days={"2026-07-01", "2026-07-03"},
            window_days=window_dates("2026-07-01", "2026-07-03"),
        )
        assert report.missing_days == ["2026-07-02"]
        assert report.present_days == ["2026-07-01", "2026-07-03"]
        # The missing day is NOT charged as a zero-coverage cell: only the 2 present
        # days are eligible, one of which hit ⇒ 1/2 = 50%.
        cov = report.personas[0].interests[0]
        assert cov.eligible_day_count == 2
        assert cov.hit_day_count == 1
        assert report.personas[0].hit_rate == 0.5

    def test_interest_added_mid_window_not_charged_for_earlier_days(self) -> None:
        # Followed only from the 3rd — days 1–2 are not eligible (no miss charged).
        persona = PersonaProfile(
            persona_key="chip",
            persona_email="persona.chip@news20.seed",
            persona_user_id="u2",
            interests=[_interest("new", "tech.ai.agents", added="2026-07-03")],
        )
        tags = [
            TagObservation(interest_id="new", story_id="s1", created_date="2026-07-03"),
        ]
        report = build_coverage_census(
            personas=[persona],
            tag_observations=tags,
            present_days={"2026-07-01", "2026-07-02", "2026-07-03"},
            window_days=window_dates("2026-07-01", "2026-07-03"),
        )
        cov = report.personas[0].interests[0]
        assert cov.eligible_day_count == 1
        assert cov.hit_day_count == 1
        assert report.personas[0].hit_rate == 1.0

    def test_below_target_hit_rate_flagged(self) -> None:
        # 2 interests over 1 present day: 1 hits, 1 dry ⇒ 50% < 60% ⇒ BELOW.
        persona = PersonaProfile(
            persona_key="founder",
            persona_email="persona.founder@news20.seed",
            persona_user_id="u3",
            interests=[
                _interest("hit", "business.vc.seed"),
                _interest("dry", "business.startups.pre-seed"),
            ],
        )
        tags = [
            TagObservation(interest_id="hit", story_id="s1", created_date="2026-07-01"),
        ]
        report = build_coverage_census(
            personas=[persona],
            tag_observations=tags,
            present_days={"2026-07-01"},
            window_days=["2026-07-01"],
        )
        assert report.personas[0].hit_rate == 0.5
        assert report.personas[0].meets_target is False
        assert report.overall_meets_target is False

    def test_no_present_days_yields_zero_rate_no_divide_by_zero(self) -> None:
        persona = PersonaProfile(
            persona_key="cricket",
            persona_email="persona.cricket@news20.seed",
            persona_user_id="u1",
            interests=[_interest("ipl", "sport.cricket.ipl")],
        )
        report = build_coverage_census(
            personas=[persona],
            tag_observations=[],
            present_days=set(),
            window_days=window_dates("2026-07-01", "2026-07-03"),
        )
        assert report.missing_days == ["2026-07-01", "2026-07-02", "2026-07-03"]
        assert report.overall_hit_rate == 0.0
        assert report.personas[0].total_cell_count == 0


class TestReportShape:
    """The stable output shape slice #10 (M4 validation) consumes."""

    def test_report_shape_is_stable(self) -> None:
        persona = PersonaProfile(
            persona_key="cricket",
            persona_email="persona.cricket@news20.seed",
            persona_user_id="u1",
            interests=[
                _interest(
                    "ipl",
                    "sport.cricket.ipl",
                    ancestors=[("sport", "sport", 0)],
                )
            ],
        )
        report = build_coverage_census(
            personas=[persona],
            tag_observations=[
                TagObservation(
                    interest_id="ipl", story_id="s1", created_date="2026-07-01"
                )
            ],
            present_days={"2026-07-01"},
            window_days=["2026-07-01"],
            generated_at_utc="2026-07-03T00:00:00+00:00",
        )
        dumped = report.model_dump()
        # Top-level contract.
        assert set(dumped) == {
            "generated_at_utc",
            "window_start_date",
            "window_end_date",
            "hit_rate_target",
            "present_days",
            "missing_days",
            "personas",
            "overall_hit_cell_count",
            "overall_total_cell_count",
            "overall_hit_rate",
            "overall_meets_target",
        }
        assert dumped["hit_rate_target"] == HIT_RATE_TARGET
        # Persona contract.
        assert set(dumped["personas"][0]) == {
            "persona_key",
            "persona_email",
            "persona_user_id",
            "interests",
            "hit_cell_count",
            "total_cell_count",
            "hit_rate",
            "meets_target",
        }
        # Interest contract.
        assert set(dumped["personas"][0]["interests"][0]) == {
            "interest_id",
            "interest_slug",
            "interest_label",
            "interest_depth",
            "eligible_day_count",
            "hit_day_count",
            "total_direct_hits",
            "is_dry",
            "per_day",
            "ladder_context",
        }
        # Nested day + ladder contracts.
        assert set(dumped["personas"][0]["interests"][0]["per_day"][0]) == {
            "census_date",
            "direct_hit_count",
        }
        assert set(dumped["personas"][0]["interests"][0]["ladder_context"][0]) == {
            "ladder_slug",
            "depth_from_interest",
            "total_direct_hits",
        }
        # Round-trips through JSON (the wire form slice #10 reads).
        assert CoverageCensusReport.model_validate_json(report.model_dump_json())

    def test_render_report_text_marks_dry_and_missing(self) -> None:
        persona = PersonaProfile(
            persona_key="founder",
            persona_email="persona.founder@news20.seed",
            persona_user_id="u3",
            interests=[_interest("dry", "business.startups.pre-seed")],
        )
        report = build_coverage_census(
            personas=[persona],
            tag_observations=[],
            present_days={"2026-07-01"},
            window_days=window_dates("2026-07-01", "2026-07-02"),
        )
        text = render_report_text(report)
        assert "DRY" in text
        assert "MISSING days (1)" in text
        assert "2026-07-02" in text
        assert "BELOW" in text


class _FakeQuery:
    """A chainable fake of the Supabase query builder that records reads.

    Every filter method returns ``self`` so ``.select().in_().gte().lt().execute()``
    chains resolve; ``execute()`` returns the pre-seeded response for the table.
    """

    def __init__(self, table_name: str, response_data: object) -> None:
        self.table_name = table_name
        self._response = MagicMock(data=response_data)

    def select(self, *_a: object, **_k: object) -> "_FakeQuery":
        return self

    def in_(self, *_a: object, **_k: object) -> "_FakeQuery":
        return self

    def gte(self, *_a: object, **_k: object) -> "_FakeQuery":
        return self

    def lt(self, *_a: object, **_k: object) -> "_FakeQuery":
        return self

    def execute(self) -> object:
        return self._response


class _FakeSupabase:
    """A fake Supabase client returning canned rows per table; tracks .table() calls.

    Deliberately exposes NO write methods — any insert/update/upsert/delete/rpc/storage
    call would raise AttributeError, which the read-only test relies on.
    """

    def __init__(self, rows_by_table: dict[str, object]) -> None:
        self._rows_by_table = rows_by_table
        self.table_calls: list[str] = []

    def table(self, name: str) -> _FakeQuery:
        self.table_calls.append(name)
        return _FakeQuery(name, self._rows_by_table.get(name, []))


class TestFetchCensusInputsSeam:
    """The read-only DB seam — one no-mock-chain pass through the real query-builder."""

    def _client(self) -> _FakeSupabase:
        return _FakeSupabase(
            {
                "users": [
                    {"user_id": "u-cricket", "user_email": "persona.cricket@news20.seed"}
                ],
                "user_interest_profile": [
                    {
                        "profile_user_id": "u-cricket",
                        "profile_interest_id": "i-ipl",
                        "profile_created_at": "2026-07-01T06:00:00+00:00",
                    }
                ],
                "interests": [
                    {
                        "interest_id": "i-ipl",
                        "interest_slug": "sport.cricket.ipl",
                        "interest_label": "IPL",
                        "depth_level": 2,
                        "parent_interest_id": "i-cricket",
                    },
                    {
                        "interest_id": "i-cricket",
                        "interest_slug": "sport.cricket",
                        "interest_label": "Cricket",
                        "depth_level": 1,
                        "parent_interest_id": "i-sport",
                    },
                    {
                        "interest_id": "i-sport",
                        "interest_slug": "sport",
                        "interest_label": "Sport",
                        "depth_level": 0,
                        "parent_interest_id": None,
                    },
                ],
                "story_interests": [
                    {
                        "story_interest_interest_id": "i-ipl",
                        "story_interest_story_id": "s1",
                        "story_interest_created_at": "2026-07-01T09:30:00+00:00",
                    },
                    {
                        "story_interest_interest_id": "i-sport",
                        "story_interest_story_id": "s2",
                        "story_interest_created_at": "2026-07-01T10:00:00+00:00",
                    },
                ],
            }
        )

    def test_fetch_builds_profiles_tags_and_present_days(self) -> None:
        client = self._client()
        personas, tags, present_days = fetch_census_inputs(
            client,
            persona_emails=["persona.cricket@news20.seed"],
            window_start_date="2026-07-01",
            window_end_date="2026-07-03",
        )
        assert len(personas) == 1
        persona = personas[0]
        assert persona.persona_user_id == "u-cricket"
        assert len(persona.interests) == 1
        ipl = persona.interests[0]
        assert ipl.interest_slug == "sport.cricket.ipl"
        assert ipl.profile_added_date == "2026-07-01"
        # Ancestors resolved leaf→root through the real parent walk.
        assert ipl.ancestor_slugs == ["sport.cricket", "sport"]
        # Tag rows narrowed + UTC-dated; present-day probe found the batch ran on 07-01.
        assert {(t.interest_id, t.created_date) for t in tags} == {
            ("i-ipl", "2026-07-01"),
            ("i-sport", "2026-07-01"),
        }
        assert present_days == {"2026-07-01"}

    def test_fetch_is_read_only_no_write_calls(self) -> None:
        client = self._client()
        fetch_census_inputs(
            client,
            persona_emails=["persona.cricket@news20.seed"],
            window_start_date="2026-07-01",
            window_end_date="2026-07-03",
        )
        # Only ever reads these tables — never writes; the fake exposes no write verbs
        # so a write attempt would already AttributeError, but pin the read surface too.
        assert set(client.table_calls) <= {
            "users",
            "user_interest_profile",
            "interests",
            "story_interests",
        }
        assert not hasattr(client, "rpc")

    def test_unseeded_persona_has_no_user_id_and_no_interests(self) -> None:
        client = self._client()
        personas, _tags, _present = fetch_census_inputs(
            client,
            persona_emails=[
                "persona.cricket@news20.seed",
                "persona.missing@news20.seed",
            ],
            window_start_date="2026-07-01",
            window_end_date="2026-07-03",
        )
        missing = next(p for p in personas if p.persona_email.endswith("missing@news20.seed"))
        assert missing.persona_user_id is None
        assert missing.interests == []
