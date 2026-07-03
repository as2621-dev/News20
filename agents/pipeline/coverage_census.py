"""Per-niche coverage census — the ≥60% hit-rate instrument (slice #5).

Measures the PRD's riskiest assumption (``plans/prd.md`` §risks: "micro-niche
ingestion can fill the feed … the fallback label is the exception") BEFORE any
niche feed UI is built. For each seeded persona's followed micro-interest it counts
the **direct-tagged** candidate stories per day — stories that carry a
``story_interests`` row *on that exact interest node* (not pool-wide stories, not
only an inherited ancestor tag) — and reports the hit rate against the 60% target
so the M4 go/no-go is a read-off, not a recount.

Two seams, cleanly split so the report shape is unit-testable with NO database:

  * :func:`build_coverage_census` — PURE over injected plain data (persona profiles,
    direct-tag observations, the pool-wide present-day set). Returns a
    :class:`CoverageCensusReport`. This is the stable shape the M4 validation slice
    (#10) consumes; it is locked by a mocked-data shape test.
  * :func:`fetch_census_inputs` — the READ-ONLY database seam: it only ever calls
    ``supabase.table(...).select(...)`` (no insert / update / upsert / delete / rpc /
    storage), so the census can never trigger ingestion or mutate the pool. It builds
    exactly the plain inputs :func:`build_coverage_census` consumes.

Date keying (the UTC-vs-local project gotcha, decided here): the census keys each
day on the **UTC calendar date of ``story_interests.story_interest_created_at``** —
the moment the daily batch stamped the tag, i.e. the *pull day*. That is the honest
"per day of real pulls" axis the ≥3-day operational run measures; a tag is written
once per (story, interest) so a cross-day-reused story counts on the pull that first
surfaced it. A day the batch never ran has zero tags pool-wide and is reported
**missing** (absence of data), distinct from a present day on which a specific
interest drew zero (a **dry niche** — the finding, not noise).

Example:
    >>> report = build_coverage_census(
    ...     personas=[...], tag_observations=[...],
    ...     present_days={"2026-07-03"}, window_days=["2026-07-03"],
    ... )
    >>> report.overall_meets_target
    False
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from agents.shared.logger import get_logger

logger = get_logger("pipeline.coverage_census")

# Reason: the PRD's de-risking bet — ≥60% of a persona's niche sections should fill
# from a DIRECT niche story (rather than an honest-fallback climb) on a given pull
# day. This is the single number the M2/M4 go/no-go reads off. Kept as one constant
# so the target lives in exactly one place (Rule 7).
HIT_RATE_TARGET: float = 0.60

# Reason: PostgREST caps a single .select() at 1000 rows by default (a known project
# gotcha). The census reads story_interests, which grows without bound as niche
# ingestion ramps — an unpaginated read would silently truncate and corrupt BOTH the
# present-day set (denominator) and the hit counts (numerator). Every DB read paginates
# through _fetch_all in pages of this size.
_PAGE_SIZE = 1000


class SeededInterest(BaseModel):
    """One micro-interest a persona follows, with its ladder ancestors resolved.

    Attributes:
        interest_id: The interest node uuid (matches ``story_interests`` FK).
        interest_slug: Dotted taxonomy slug (e.g. ``sport.cricket.ipl``).
        interest_label: Human-readable taxonomy label.
        interest_depth: ``depth_level`` (0 root … 3 leaf).
        profile_added_date: UTC date the persona started following this interest
            (``profile_created_at``); the interest is only scored on present days on
            or after this date, so an interest added mid-window is not charged a
            "miss" for days before it existed.
        ancestor_interest_ids: Parent→grandparent→… interest ids up the ladder, for
            the fallback-context counts (nearest ancestor first).
        ancestor_slugs: The matching ancestor slugs, index-aligned with the ids.
    """

    interest_id: str = Field(..., description="Interest node uuid")
    interest_slug: str = Field(..., description="Dotted taxonomy slug")
    interest_label: str = Field(..., description="Human-readable label")
    interest_depth: int = Field(..., description="depth_level 0..3")
    profile_added_date: str = Field(..., description="UTC date followed (YYYY-MM-DD)")
    ancestor_interest_ids: list[str] = Field(default_factory=list)
    ancestor_slugs: list[str] = Field(default_factory=list)


class PersonaProfile(BaseModel):
    """A seeded persona and the micro-interests it follows."""

    persona_key: str = Field(..., description="Stable short key (founder/cricket/chip)")
    persona_email: str = Field(..., description="Auth user email")
    persona_user_id: str | None = Field(
        default=None, description="Resolved user_id, or None if the persona is unseeded"
    )
    interests: list[SeededInterest] = Field(default_factory=list)


class TagObservation(BaseModel):
    """One direct ``story_interests`` tag row, narrowed to what the census counts.

    A tag row means the interest node's own ladder surfaced the story (ancestor
    tagging only ever walks UP, so a row on a leaf interest is a genuine direct/niche
    hit — never an inherited one). ``created_date`` is the UTC date of
    ``story_interest_created_at`` (the pull day).
    """

    interest_id: str = Field(..., description="story_interest_interest_id")
    story_id: str = Field(..., description="story_interest_story_id")
    created_date: str = Field(..., description="UTC pull date (YYYY-MM-DD)")


class InterestDayCount(BaseModel):
    """Direct-hit count for one interest on one present day."""

    census_date: str = Field(..., description="UTC date (YYYY-MM-DD)")
    direct_hit_count: int = Field(..., description="Distinct stories tagged this day")


class LadderLevelCount(BaseModel):
    """Total direct hits at one ladder level (the fallback-context read).

    ``depth`` counts UP from the followed interest: 0 = the interest itself,
    1 = its parent, 2 = its grandparent. A parent/root total materially above the
    leaf total is what an honest-fallback climb would draw on.
    """

    ladder_slug: str = Field(..., description="Interest slug at this ladder level")
    depth_from_interest: int = Field(..., description="0 self, 1 parent, 2 grandparent")
    total_direct_hits: int = Field(..., description="Distinct stories over the window")


class InterestCoverage(BaseModel):
    """Per-interest coverage: per-day direct counts, totals, and ladder context."""

    interest_id: str
    interest_slug: str
    interest_label: str
    interest_depth: int
    eligible_day_count: int = Field(
        ..., description="Present days on/after this interest was followed"
    )
    hit_day_count: int = Field(..., description="Eligible days with ≥1 direct hit")
    total_direct_hits: int = Field(..., description="Distinct stories over the window")
    is_dry: bool = Field(..., description="Zero direct hits across all eligible days")
    per_day: list[InterestDayCount] = Field(default_factory=list)
    ladder_context: list[LadderLevelCount] = Field(default_factory=list)


class PersonaCoverage(BaseModel):
    """Per-persona coverage roll-up plus its hit rate vs the target."""

    persona_key: str
    persona_email: str
    persona_user_id: str | None
    interests: list[InterestCoverage] = Field(default_factory=list)
    hit_cell_count: int = Field(..., description="(interest, eligible-day) cells hit")
    total_cell_count: int = Field(..., description="(interest, eligible-day) cells")
    hit_rate: float = Field(..., description="hit_cell_count / total_cell_count, 0..1")
    meets_target: bool = Field(..., description="hit_rate ≥ target")


class CoverageCensusReport(BaseModel):
    """The full census report — the M4-validation-stable shape (slice #10 consumes).

    The hit rate is the fraction of ``(followed-interest, eligible-present-day)``
    cells that drew at least one DIRECT niche story. Each interest is one feed
    section, so this answers "would each persona's sections fill from direct niche
    stories on a given day?" — exactly the ≥60% bet.
    """

    generated_at_utc: str
    window_start_date: str
    window_end_date: str
    hit_rate_target: float = HIT_RATE_TARGET
    present_days: list[str] = Field(default_factory=list)
    missing_days: list[str] = Field(default_factory=list)
    personas: list[PersonaCoverage] = Field(default_factory=list)
    overall_hit_cell_count: int = 0
    overall_total_cell_count: int = 0
    overall_hit_rate: float = 0.0
    overall_meets_target: bool = False


def window_dates(start_date: str, end_date: str) -> list[str]:
    """Every UTC calendar date from ``start_date`` to ``end_date`` inclusive.

    Args:
        start_date: Inclusive lower bound, ``YYYY-MM-DD``.
        end_date: Inclusive upper bound, ``YYYY-MM-DD``.

    Returns:
        Ascending list of ISO date strings (empty if start is after end).

    Example:
        >>> window_dates("2026-07-01", "2026-07-03")
        ['2026-07-01', '2026-07-02', '2026-07-03']
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def build_coverage_census(
    *,
    personas: list[PersonaProfile],
    tag_observations: list[TagObservation],
    present_days: set[str],
    window_days: list[str],
    hit_rate_target: float = HIT_RATE_TARGET,
    generated_at_utc: str | None = None,
) -> CoverageCensusReport:
    """Build the coverage census report from plain, pre-fetched inputs (pure).

    Args:
        personas: The seeded personas with their followed interests (ancestors
            resolved for the ladder-context read).
        tag_observations: Direct ``story_interests`` tag rows for every followed
            interest AND its ancestors, over the window (interest_id, story_id, UTC
            pull date). A row on an interest = a direct hit for that interest.
        present_days: UTC dates on which the batch actually ran (≥1 tag pool-wide).
            A window day NOT in this set is reported missing (absence of data ≠ zero
            coverage).
        window_days: The full ordered set of UTC dates the census spans (from
            :func:`window_dates`).
        hit_rate_target: The ≥ bar the hit rate is judged against (default 60%).
        generated_at_utc: Override for the report timestamp (tests); defaults to now.

    Returns:
        A fully-populated :class:`CoverageCensusReport`.

    Example:
        >>> build_coverage_census(
        ...     personas=[], tag_observations=[],
        ...     present_days=set(), window_days=["2026-07-03"],
        ... ).overall_hit_rate
        0.0
    """
    present_in_window = sorted(present_days & set(window_days))
    missing_days = sorted(set(window_days) - present_days)

    # Distinct stories per (interest_id, day) — direct hits. A set dedups the ancestor
    # fan-out so one story matched by two of a persona's interests is not double-counted
    # within a single interest cell.
    stories_by_interest_day: dict[tuple[str, str], set[str]] = {}
    stories_by_interest_total: dict[str, set[str]] = {}
    for obs in tag_observations:
        stories_by_interest_day.setdefault(
            (obs.interest_id, obs.created_date), set()
        ).add(obs.story_id)
        stories_by_interest_total.setdefault(obs.interest_id, set()).add(obs.story_id)

    persona_coverages: list[PersonaCoverage] = []
    overall_hit_cells = 0
    overall_total_cells = 0

    for persona in personas:
        interest_coverages: list[InterestCoverage] = []
        persona_hit_cells = 0
        persona_total_cells = 0

        for interest in persona.interests:
            # Only score present days on/after the interest was followed — an interest
            # added mid-window is not charged a miss for days before it existed.
            eligible_days = [
                day
                for day in present_in_window
                if day >= interest.profile_added_date
            ]
            per_day: list[InterestDayCount] = []
            hit_days = 0
            for day in eligible_days:
                count = len(
                    stories_by_interest_day.get((interest.interest_id, day), set())
                )
                per_day.append(
                    InterestDayCount(census_date=day, direct_hit_count=count)
                )
                if count > 0:
                    hit_days += 1

            total_direct = len(
                stories_by_interest_total.get(interest.interest_id, set())
            )

            # Ladder context: the interest itself + each ancestor, so the operator can
            # see whether an honest-fallback climb would have material to climb to.
            ladder_context = [
                LadderLevelCount(
                    ladder_slug=interest.interest_slug,
                    depth_from_interest=0,
                    total_direct_hits=total_direct,
                )
            ]
            for offset, (anc_id, anc_slug) in enumerate(
                zip(interest.ancestor_interest_ids, interest.ancestor_slugs), start=1
            ):
                ladder_context.append(
                    LadderLevelCount(
                        ladder_slug=anc_slug,
                        depth_from_interest=offset,
                        total_direct_hits=len(
                            stories_by_interest_total.get(anc_id, set())
                        ),
                    )
                )

            interest_coverages.append(
                InterestCoverage(
                    interest_id=interest.interest_id,
                    interest_slug=interest.interest_slug,
                    interest_label=interest.interest_label,
                    interest_depth=interest.interest_depth,
                    eligible_day_count=len(eligible_days),
                    hit_day_count=hit_days,
                    total_direct_hits=total_direct,
                    is_dry=total_direct == 0,
                    per_day=per_day,
                    ladder_context=ladder_context,
                )
            )
            persona_hit_cells += hit_days
            persona_total_cells += len(eligible_days)

        persona_rate = (
            persona_hit_cells / persona_total_cells if persona_total_cells else 0.0
        )
        persona_coverages.append(
            PersonaCoverage(
                persona_key=persona.persona_key,
                persona_email=persona.persona_email,
                persona_user_id=persona.persona_user_id,
                interests=interest_coverages,
                hit_cell_count=persona_hit_cells,
                total_cell_count=persona_total_cells,
                hit_rate=persona_rate,
                meets_target=persona_rate >= hit_rate_target,
            )
        )
        overall_hit_cells += persona_hit_cells
        overall_total_cells += persona_total_cells

    overall_rate = (
        overall_hit_cells / overall_total_cells if overall_total_cells else 0.0
    )
    generated = generated_at_utc or datetime.now(timezone.utc).isoformat()
    report = CoverageCensusReport(
        generated_at_utc=generated,
        window_start_date=window_days[0] if window_days else "",
        window_end_date=window_days[-1] if window_days else "",
        hit_rate_target=hit_rate_target,
        present_days=present_in_window,
        missing_days=missing_days,
        personas=persona_coverages,
        overall_hit_cell_count=overall_hit_cells,
        overall_total_cell_count=overall_total_cells,
        overall_hit_rate=overall_rate,
        overall_meets_target=overall_rate >= hit_rate_target,
    )
    logger.info(
        "coverage_census_built",
        personas=len(persona_coverages),
        present_days=len(present_in_window),
        missing_days=len(missing_days),
        overall_hit_rate=round(overall_rate, 4),
        overall_meets_target=report.overall_meets_target,
    )
    return report


def render_report_text(report: CoverageCensusReport) -> str:
    """Render a census report as a human-readable operator report (pure).

    The format is a stable operator read, not a machine contract — slice #10 consumes
    the :class:`CoverageCensusReport` model (or its ``model_dump_json``), not this text.

    Args:
        report: A built :class:`CoverageCensusReport`.

    Returns:
        A multi-line report string.
    """
    lines: list[str] = []
    target_pct = f"{report.hit_rate_target * 100:.0f}%"
    lines.append("=" * 72)
    lines.append("PER-NICHE COVERAGE CENSUS")
    lines.append(f"generated: {report.generated_at_utc}")
    lines.append(
        f"window: {report.window_start_date} → {report.window_end_date} (UTC pull dates)"
    )
    lines.append(
        f"present days ({len(report.present_days)}): "
        f"{', '.join(report.present_days) or '(none)'}"
    )
    lines.append(
        f"MISSING days ({len(report.missing_days)}) — no batch run: "
        f"{', '.join(report.missing_days) or '(none)'}"
    )
    lines.append("=" * 72)

    for persona in report.personas:
        verdict = "PASS" if persona.meets_target else "BELOW"
        lines.append("")
        lines.append(
            f"[{persona.persona_key}] {persona.persona_email}"
            + ("" if persona.persona_user_id else "  (UNSEEDED — no user_id)")
        )
        lines.append(
            f"  hit rate {persona.hit_rate * 100:5.1f}% "
            f"({persona.hit_cell_count}/{persona.total_cell_count} interest-day cells) "
            f"vs {target_pct}  → {verdict}"
        )
        for interest in persona.interests:
            flag = "  DRY" if interest.is_dry else ""
            per_day_str = " ".join(
                f"{d.census_date[5:]}={d.direct_hit_count}" for d in interest.per_day
            )
            ladder_str = " ".join(
                f"{lvl.ladder_slug}:{lvl.total_direct_hits}"
                for lvl in interest.ladder_context
            )
            lines.append(
                f"    {interest.interest_slug:<28} "
                f"hits={interest.total_direct_hits:>3} "
                f"days={interest.hit_day_count}/{interest.eligible_day_count}{flag}"
            )
            if per_day_str:
                lines.append(f"        per-day: {per_day_str}")
            lines.append(f"        ladder:  {ladder_str}")

    lines.append("")
    lines.append("-" * 72)
    overall_verdict = "PASS" if report.overall_meets_target else "BELOW TARGET"
    lines.append(
        f"OVERALL hit rate {report.overall_hit_rate * 100:5.1f}% "
        f"({report.overall_hit_cell_count}/{report.overall_total_cell_count}) "
        f"vs {target_pct}  → {overall_verdict}"
    )
    lines.append("-" * 72)
    return "\n".join(lines)


def _utc_date_of(timestamp_str: str) -> str:
    """UTC calendar date (``YYYY-MM-DD``) of an ISO-8601 timestamp string.

    Normalizes a trailing ``Z``, applies the UTC offset, and returns the UTC date —
    the pull-day key. A naive timestamp is assumed already-UTC (Supabase stores
    ``timestamptz`` and returns an offset, so this is a defensive fallback).

    Example:
        >>> _utc_date_of("2026-07-03T23:30:00-05:00")
        '2026-07-04'
    """
    normalized = timestamp_str.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _fetch_all(query_factory: Any) -> list[dict[str, Any]]:
    """Read every row of a PostgREST select, paginating past the 1000-row cap.

    A single ``.select().execute()`` returns at most 1000 rows (the PostgREST
    default), so an unpaginated read silently truncates on a busy day and corrupts
    the census counts. This walks ``.range()`` pages until a short page ends the read.

    Args:
        query_factory: A zero-arg callable returning a FRESH, fully-filtered query
            builder (pre-``.range()``) — a builder is single-use per ``execute()``, so
            each page needs a new one.

    Returns:
        All rows across every page.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = query_factory().range(offset, offset + _PAGE_SIZE - 1).execute()
        page = getattr(response, "data", None) or []
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def fetch_census_inputs(
    supabase_client: Any,
    *,
    persona_emails: list[str],
    window_start_date: str,
    window_end_date: str,
) -> tuple[list[PersonaProfile], list[TagObservation], set[str]]:
    """Read-only DB seam: build the plain inputs :func:`build_coverage_census` needs.

    This function performs ONLY ``.select()`` reads — it never inserts, updates,
    upserts, deletes, calls an rpc, or touches storage, so running the census can
    never trigger ingestion or mutate the shared pool.

    Steps (all reads):
      1. ``users``           → resolve each persona email to its user_id.
      2. ``user_interest_profile`` → the followed interests + when they were followed.
      3. ``interests``       → the taxonomy (slug/label/depth/parent) to resolve each
         followed leaf's ladder ancestors.
      4. ``story_interests`` → direct tag rows (interest_id, story_id, created_at) for
         every followed interest AND its ancestors, within the window.
      5. ``story_interests`` (probe) → the distinct UTC dates any tag was created
         pool-wide in the window = the days a batch actually ran (present days).

    Args:
        supabase_client: A Supabase client (service role in prod; a mock in tests).
        persona_emails: The persona auth emails to census.
        window_start_date: Inclusive UTC lower bound, ``YYYY-MM-DD``.
        window_end_date: Inclusive UTC upper bound, ``YYYY-MM-DD``.

    Returns:
        ``(personas, tag_observations, present_days)`` — the pure builder's inputs.
    """
    # Window as UTC timestamptz bounds. The upper bound is the day AFTER window_end so
    # the whole end-day (up to 23:59:59Z) is included with a half-open < comparison.
    start_ts = f"{window_start_date}T00:00:00+00:00"
    end_exclusive_ts = (
        (date.fromisoformat(window_end_date) + timedelta(days=1)).isoformat()
        + "T00:00:00+00:00"
    )

    # ── 1. Resolve persona user_ids by email ──
    user_rows = _fetch_all(
        lambda: supabase_client.table("users")
        .select("user_id, user_email")
        .in_("user_email", persona_emails)
    )
    user_id_by_email = {
        str(row["user_email"]).lower(): str(row["user_id"]) for row in user_rows
    }

    # ── 2. Followed interests per persona (with when they were followed) ──
    persona_user_ids = [
        user_id_by_email[email.lower()]
        for email in persona_emails
        if email.lower() in user_id_by_email
    ]
    profile_rows: list[dict[str, Any]] = []
    if persona_user_ids:
        profile_rows = _fetch_all(
            lambda: supabase_client.table("user_interest_profile")
            .select("profile_user_id, profile_interest_id, profile_created_at")
            .in_("profile_user_id", persona_user_ids)
        )

    # ── 3. Taxonomy map (for ladder-ancestor resolution) ──
    interest_rows = _fetch_all(
        lambda: supabase_client.table("interests").select(
            "interest_id, interest_slug, interest_label, depth_level, parent_interest_id"
        )
    )
    node_by_id: dict[str, dict[str, Any]] = {
        str(row["interest_id"]): row for row in interest_rows
    }

    def _ancestors(interest_id: str) -> list[dict[str, Any]]:
        """Walk parent links up to the root (nearest ancestor first)."""
        chain: list[dict[str, Any]] = []
        current = node_by_id.get(interest_id)
        seen: set[str] = {interest_id}
        while current is not None:
            parent_id = current.get("parent_interest_id")
            if not parent_id or str(parent_id) in seen:
                break
            parent = node_by_id.get(str(parent_id))
            if parent is None:
                break
            chain.append(parent)
            seen.add(str(parent_id))
            current = parent
        return chain

    # Assemble persona profiles.
    profiles_by_user: dict[str, list[SeededInterest]] = {}
    all_relevant_interest_ids: set[str] = set()
    for row in profile_rows:
        interest_id = str(row["profile_interest_id"])
        node = node_by_id.get(interest_id)
        if node is None:
            logger.warning(
                "census_profile_interest_missing_from_taxonomy",
                interest_id=interest_id,
                fix_suggestion="A followed interest is absent from the interests "
                "table — backfill the taxonomy row so its coverage can be counted.",
            )
            continue
        ancestors = _ancestors(interest_id)
        seeded = SeededInterest(
            interest_id=interest_id,
            interest_slug=str(node["interest_slug"]),
            interest_label=str(node["interest_label"]),
            interest_depth=int(node["depth_level"]),
            profile_added_date=_utc_date_of(str(row["profile_created_at"])),
            ancestor_interest_ids=[str(a["interest_id"]) for a in ancestors],
            ancestor_slugs=[str(a["interest_slug"]) for a in ancestors],
        )
        profiles_by_user.setdefault(str(row["profile_user_id"]), []).append(seeded)
        all_relevant_interest_ids.add(interest_id)
        all_relevant_interest_ids.update(seeded.ancestor_interest_ids)

    personas: list[PersonaProfile] = []
    for email in persona_emails:
        user_id = user_id_by_email.get(email.lower())
        interests = sorted(
            profiles_by_user.get(user_id or "", []),
            key=lambda si: si.interest_slug,
        )
        personas.append(
            PersonaProfile(
                persona_key=email.split("@")[0].split(".")[-1],
                persona_email=email,
                persona_user_id=user_id,
                interests=interests,
            )
        )

    # ── 4. Direct tag rows for the relevant interests, within the window ──
    tag_observations: list[TagObservation] = []
    if all_relevant_interest_ids:
        relevant_ids = sorted(all_relevant_interest_ids)
        tag_rows = _fetch_all(
            lambda: supabase_client.table("story_interests")
            .select(
                "story_interest_interest_id, story_interest_story_id, "
                "story_interest_created_at"
            )
            .in_("story_interest_interest_id", relevant_ids)
            .gte("story_interest_created_at", start_ts)
            .lt("story_interest_created_at", end_exclusive_ts)
        )
        for row in tag_rows:
            tag_observations.append(
                TagObservation(
                    interest_id=str(row["story_interest_interest_id"]),
                    story_id=str(row["story_interest_story_id"]),
                    created_date=_utc_date_of(str(row["story_interest_created_at"])),
                )
            )

    # ── 5. Present-day probe: dates any tag was created POOL-WIDE in the window ──
    # A day the batch ran has ≥1 tag pool-wide; a window day with none = batch never
    # ran (missing), distinct from a persona interest that drew zero (dry niche).
    present_rows = _fetch_all(
        lambda: supabase_client.table("story_interests")
        .select("story_interest_created_at")
        .gte("story_interest_created_at", start_ts)
        .lt("story_interest_created_at", end_exclusive_ts)
    )
    present_days = {
        _utc_date_of(str(row["story_interest_created_at"])) for row in present_rows
    }

    logger.info(
        "coverage_census_inputs_fetched",
        personas=len(personas),
        seeded_personas=sum(1 for p in personas if p.persona_user_id),
        relevant_interests=len(all_relevant_interest_ids),
        tag_rows=len(tag_observations),
        present_days=len(present_days),
    )
    return personas, tag_observations, present_days
