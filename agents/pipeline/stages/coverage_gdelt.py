"""GDELT coverage census stage — the adaptive Detail "Coverage" tab (Phase 2c SP2).

Turns one canonical story into a mode-correct ``CoverageReport`` (partisan L·C·R
+ blindspot, OR reach covered-by-N + momentum + who-broke-it) by running a
**GDELT DOC 2.0 census** of the distinct outlets covering the story, then
resolving each domain to a bias lean against an **injected** ``outlets_lookup``.

WHY a separate census (Decision #4): ``story_sources`` records the few clustered
articles we actually *scripted* from; the Coverage tab needs the *whole* coverage
landscape, which only a broad GDELT query surfaces. The census is read-only —
this stage never writes ``story_sources`` or ``outlets``.

REUSE (Rule 2/8), not reinvention:
  • The keyless ``GdeltDocAdapter`` (``agents/ingestion/adapters/gdelt_doc.py``)
    is **injected** — we call ``adapter.search(...)`` and read the already-
    lowercased ``candidate_outlet_domain`` off each ``CandidateStory``. No new
    GDELT client; the adapter owns the ≤1-req/5s throttle, ``sort=hybridrel``,
    ``maxrecords ≤ 250`` and the 1–3d timespan.
  • ``derive_blindspot_lean`` (``persist_helpers``) computes the >70%-one-side
    blindspot from a counts dict — reused verbatim on the rated set.

PURITY: ``build_coverage_report`` is pure over its injected ``adapter`` and
``outlets_lookup`` — it performs no live network or DB read *itself* (the adapter
does the I/O, the lookup is a plain dict). GDELT failure is **non-fatal**: an
``AdapterFetchError`` is caught and the report falls back to the story's own
``covering_outlets``-derived counts (Decision #3 — coverage degrades, never
errors).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.ingestion.adapters.gdelt_doc import GdeltDocAdapter
from agents.ingestion.models import CandidateStory, CanonicalStory
from agents.pipeline.models import (
    BiasLean,
    CoverageMode,
    CoverageMomentum,
    CoverageReport,
)
from agents.pipeline.persist_helpers import derive_blindspot_lean
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.coverage_gdelt")

# Reason: segments whose coverage is genuinely contested map to the partisan
# (L·C·R + blindspot) framing; every other segment frames coverage as reach
# (covered-by-N + momentum). Default is partisan for geopolitics, reach otherwise
# (Decision #3). Code, never the LLM, picks the mode (Rule 5).
_PARTISAN_SEGMENT_SLUGS: frozenset[str] = frozenset({"geopolitics"})

# Reason: how far back the coverage census looks. Clamped into the adapter's
# supported 1–3d timespan window; 2 days balances recall against picking up
# stale/unrelated articles for a fresh story.
_COVERAGE_LOOKBACK_DAYS = 2

# Reason: domains that are not first-party news outlets — aggregators, social
# silos, and video/PR wires — would inflate the outlet census without adding a
# distinct editorial voice. Dropped before counting (the "noise" filter).
_NOISE_DOMAINS: frozenset[str] = frozenset(
    {
        "news.google.com",
        "google.com",
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "twitter.com",
        "x.com",
        "reddit.com",
        "t.co",
        "msn.com",
        "yahoo.com",
        "news.yahoo.com",
        "flipboard.com",
        "prnewswire.com",
        "globenewswire.com",
        "businesswire.com",
    }
)

# Reason: GDELT reports per-country (and per-language) editions on subdomains and
# foreign ccTLDs; collapsing/dropping them keeps the census to distinct primary
# US/UK English outlets so a single outlet's 6 regional editions count once, not
# six times. Foreign-edition ccTLDs we drop entirely.
_FOREIGN_CCTLD_SUFFIXES: tuple[str, ...] = (
    ".ru",
    ".cn",
    ".ir",
    ".in",
    ".pk",
    ".jp",
    ".kr",
    ".de",
    ".fr",
    ".es",
    ".it",
    ".br",
    ".mx",
    ".ng",
    ".za",
)

# Reason: outlet families that publish many affiliate subdomains (local TV/radio
# groups, wire mirrors). Map any subdomain of these to the registrable apex so
# affiliate editions collapse to a single outlet in the census.
_AFFILIATE_APEX_DOMAINS: frozenset[str] = frozenset(
    {
        "yahoo.com",
        "msn.com",
        "aol.com",
        "patch.com",
        "blogspot.com",
        "wordpress.com",
        "substack.com",
    }
)

# Reason: a side is a blindspot only when coverage is concentrated enough to call
# it (matches persist_helpers' >70%-on-the-other-sides rule, which needs >= 4
# rated outlets to be meaningful).
_MIN_RATED_OUTLETS_FOR_BLINDSPOT = 4

# Reason: reach-mode momentum buckets keyed off the GDELT seendate spread — a
# tight cluster of fresh datestamps reads "breaking"; a wide spread is "settled".
_BREAKING_SPREAD_HOURS = 6.0
_DEVELOPING_SPREAD_HOURS = 36.0

# Reason: the Coverage "notable outlets" strip shows at most 5 names (matches
# CoverageReport.coverage_notable_outlet_names max_length=5).
_MAX_NOTABLE_OUTLETS = 5


def coverage_mode_for_segment(story_segment_slug: str) -> CoverageMode:
    """Pick the deterministic Coverage framing for a story's segment (Rule 5).

    Args:
        story_segment_slug: The story's ``story_segment_slug`` (a ``segment_slug``
            enum value, e.g. ``"geopolitics"`` / ``"markets"`` / ``"sport"``).

    Returns:
        ``"partisan"`` for contested segments (geopolitics), else ``"reach"``.

    Example:
        >>> coverage_mode_for_segment("geopolitics")
        'partisan'
        >>> coverage_mode_for_segment("sport")
        'reach'
    """
    return "partisan" if story_segment_slug in _PARTISAN_SEGMENT_SLUGS else "reach"


def normalize_coverage_domain(outlet_domain: str) -> str | None:
    """Normalize a GDELT domain for the census, or drop it (returns None).

    Drops aggregator/social noise and foreign-edition ccTLDs; collapses affiliate
    subdomains (``finance.yahoo.com`` → ``yahoo.com``) and a leading ``www.`` so
    one outlet counts once.

    Args:
        outlet_domain: The bare lowercased domain GDELT reported (already
            lowercased on ``CandidateStory.candidate_outlet_domain``).

    Returns:
        The normalized registrable domain, or ``None`` if the domain is noise or
        a dropped foreign edition.

    Example:
        >>> normalize_coverage_domain("finance.yahoo.com")
        'yahoo.com'
        >>> normalize_coverage_domain("rt.ru") is None
        True
    """
    domain = outlet_domain.strip().lower().rstrip(".")
    if not domain:
        return None
    if domain.startswith("www."):
        domain = domain[len("www.") :]
    if domain in _NOISE_DOMAINS:
        return None
    if domain.endswith(_FOREIGN_CCTLD_SUFFIXES):
        return None
    # Reason: collapse an affiliate subdomain to its apex so regional/section
    # editions of one outlet are not over-counted as distinct outlets.
    for apex in _AFFILIATE_APEX_DOMAINS:
        if domain == apex or domain.endswith("." + apex):
            return apex
    return domain


def _distinct_coverage_domains(candidates: list[CandidateStory]) -> list[str]:
    """Normalize + de-duplicate covering domains, preserving first-seen order.

    Order is stable (insertion order of the first appearance) so downstream
    notable-outlet selection is deterministic for a given GDELT response.
    """
    seen: dict[str, None] = {}
    for candidate in candidates:
        normalized = normalize_coverage_domain(candidate.candidate_outlet_domain)
        if normalized is not None:
            seen.setdefault(normalized, None)
    return list(seen.keys())


def _count_leans(
    domains: list[str], outlets_lookup: dict[str, BiasLean]
) -> dict[str, int]:
    """Bucket rated domains into a left/center/right/total counts dict.

    Only domains present in the injected ``outlets_lookup`` are rated and counted
    toward the partisan split; unrated domains still count toward ``total`` (the
    distinct-outlet census) but not toward a lean. Shape matches what
    ``derive_blindspot_lean`` consumes.
    """
    counts = {"left": 0, "center": 0, "right": 0, "total": len(domains)}
    for domain in domains:
        lean = outlets_lookup.get(domain)
        if lean is not None:
            counts[lean] += 1
    return counts


def _earliest_candidate(candidates: list[CandidateStory]) -> CandidateStory | None:
    """The candidate with the earliest publish/seen time (who broke the story)."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: c.candidate_published_utc)


def _derive_momentum(candidates: list[CandidateStory]) -> CoverageMomentum:
    """Bucket reach momentum from the spread of GDELT seendates.

    A tight burst of fresh datestamps reads ``"breaking"``; a moderate spread is
    ``"developing"``; a wide spread is ``"settled"``. Empty/single-article inputs
    default to ``"breaking"`` (a just-surfaced story).
    """
    times = [c.candidate_published_utc for c in candidates]
    if len(times) < 2:
        return "breaking"
    spread_hours = (max(times) - min(times)).total_seconds() / 3600.0
    if spread_hours <= _BREAKING_SPREAD_HOURS:
        return "breaking"
    if spread_hours <= _DEVELOPING_SPREAD_HOURS:
        return "developing"
    return "settled"


def _display_name(candidate: CandidateStory, normalized_domain: str) -> str:
    """Best display name for an outlet (GDELT name if present, else the domain)."""
    name = (candidate.candidate_outlet_name or "").strip()
    # Reason: the GDELT adapter defaults candidate_outlet_name to the bare domain,
    # so prefer a genuine name only when it differs from the raw domain.
    if name and name.lower() != candidate.candidate_outlet_domain.strip().lower():
        return name
    return normalized_domain


def _notable_outlet_names(
    candidates: list[CandidateStory], distinct_domains: list[str]
) -> list[str]:
    """Up to 5 distinct notable outlet display names, in census order.

    One name per normalized outlet, taken from the first candidate that maps to
    that outlet. ``distinct_domains`` preserves GDELT's own ``hybridrel``
    (relevance+recency) ordering, so the most relevant outlets lead the strip;
    "who broke it" is reported separately via ``coverage_originating_outlet_name``
    (earliest seendate), which need not be the first notable name.
    """
    name_by_domain: dict[str, str] = {}
    for candidate in candidates:
        normalized = normalize_coverage_domain(candidate.candidate_outlet_domain)
        if normalized is not None and normalized not in name_by_domain:
            name_by_domain[normalized] = _display_name(candidate, normalized)
    names: list[str] = []
    for domain in distinct_domains:
        if domain in name_by_domain:
            names.append(name_by_domain[domain])
        if len(names) >= _MAX_NOTABLE_OUTLETS:
            break
    return names


def _coverage_query(story: CanonicalStory) -> str:
    """Build the GDELT census query for a story (its representative headline).

    v1 queries on the canonical title (Decision OQ#1 — entity-expanded clustering
    is a later refinement). The adapter quotes/escapes nothing extra; the title
    is passed through as GDELT's free-text query.
    """
    return story.canonical_title.strip()


def _build_partisan_report(
    distinct_domains: list[str],
    outlets_lookup: dict[str, BiasLean],
) -> CoverageReport:
    """Assemble a partisan-mode ``CoverageReport`` from the census domains."""
    counts = _count_leans(distinct_domains, outlets_lookup)
    rated_total = counts["left"] + counts["center"] + counts["right"]
    # Reason: the blindspot share math is only meaningful on the RATED subset —
    # pass a counts dict whose total is the rated outlets, not the full census
    # (unrated outlets have no lean and must not dilute the share denominator).
    blindspot_lean: BiasLean | None = None
    if rated_total >= _MIN_RATED_OUTLETS_FOR_BLINDSPOT:
        rated_counts = {
            "left": counts["left"],
            "center": counts["center"],
            "right": counts["right"],
            "total": rated_total,
        }
        derived = derive_blindspot_lean(rated_counts)
        # derive_blindspot_lean returns a bias_lean string or None — narrow to BiasLean.
        if derived in ("left", "center", "right"):
            blindspot_lean = derived  # type: ignore[assignment]
    return CoverageReport(
        coverage_mode="partisan",
        coverage_left_count=counts["left"],
        coverage_center_count=counts["center"],
        coverage_right_count=counts["right"],
        coverage_outlet_count=counts["total"],
        blindspot_lean=blindspot_lean,
    )


def _build_reach_report(
    candidates: list[CandidateStory],
    distinct_domains: list[str],
) -> CoverageReport:
    """Assemble a reach-mode ``CoverageReport`` from the census candidates."""
    earliest = _earliest_candidate(candidates)
    originating_name: str | None = None
    if earliest is not None:
        originating_domain = normalize_coverage_domain(earliest.candidate_outlet_domain)
        if originating_domain is not None:
            originating_name = _display_name(earliest, originating_domain)
    return CoverageReport(
        coverage_mode="reach",
        coverage_outlet_count=len(distinct_domains),
        coverage_momentum=_derive_momentum(candidates),
        coverage_originating_outlet_name=originating_name,
        coverage_notable_outlet_names=_notable_outlet_names(
            candidates, distinct_domains
        ),
    )


def _fallback_report(
    story: CanonicalStory,
    coverage_mode: CoverageMode,
    outlets_lookup: dict[str, BiasLean],
) -> CoverageReport:
    """Degrade to the story's own ``covering_outlets`` when GDELT is unavailable.

    Decision #3: a failed/empty census never errors and never silently reports
    zero coverage — it falls back to the clustered covering outlets we already
    have. Partisan mode still emits L·C·R + blindspot; reach mode emits the
    distinct-outlet count (momentum/originating are unknown without seendates).
    """
    distinct = [
        normalized
        for outlet in story.covering_outlets
        if (normalized := normalize_coverage_domain(outlet)) is not None
    ]
    # Reason: de-dup while preserving order (covering_outlets is already distinct,
    # but normalization can collapse two affiliate editions into one apex).
    distinct = list(dict.fromkeys(distinct))
    if coverage_mode == "partisan":
        return _build_partisan_report(distinct, outlets_lookup)
    return CoverageReport(
        coverage_mode="reach",
        coverage_outlet_count=len(distinct),
        coverage_notable_outlet_names=distinct[:_MAX_NOTABLE_OUTLETS],
    )


async def build_coverage_report(
    story: CanonicalStory,
    story_segment_slug: str,
    outlets_lookup: dict[str, BiasLean],
    adapter: GdeltDocAdapter,
) -> CoverageReport:
    """Build a mode-correct GDELT coverage census for one story.

    Flow: ``adapter.search`` the story headline → normalize + de-dup the covering
    domains (drop foreign/noise, collapse affiliate subdomains) → resolve each
    against ``outlets_lookup`` → counts. ``coverage_mode`` is chosen
    deterministically by ``story_segment_slug`` (Rule 5). Partisan mode emits
    L·C·R counts + the >70%-one-side blindspot; reach mode emits the distinct
    outlet count + momentum + who-broke-it + up to 5 notable outlets.

    GDELT failure is **non-fatal** (Decision #3): on ``AdapterFetchError`` the
    report degrades to the story's own ``covering_outlets``, logging a
    ``fix_suggestion`` — it never raises and never silently reports zero.

    This function is **pure** over its injected ``adapter`` + ``outlets_lookup``:
    the adapter performs the (throttled) GDELT I/O, the lookup is a plain
    domain→lean dict (NOT a live DB read).

    Args:
        story: The canonical story whose coverage to census.
        story_segment_slug: The story's ``story_segment_slug`` — fixes the mode.
        outlets_lookup: Injected static ``{outlet_domain: bias_lean}`` map
            (seeded ``outlets`` table; NOT read live here).
        adapter: The shared ``GdeltDocAdapter`` (owns the ≤1-req/5s throttle).

    Returns:
        A populated ``CoverageReport`` in the segment-correct ``coverage_mode``.

    Example:
        >>> # report = await build_coverage_report(story, "geopolitics", lookup, adapter)
        >>> # report.coverage_mode
        >>> # 'partisan'
    """
    coverage_mode = coverage_mode_for_segment(story_segment_slug)
    since_utc = datetime.now(timezone.utc) - timedelta(days=_COVERAGE_LOOKBACK_DAYS)
    coverage_query = _coverage_query(story)

    logger.info(
        "coverage_report_started",
        story_id=story.canonical_story_id,
        story_segment_slug=story_segment_slug,
        coverage_mode=coverage_mode,
        coverage_query=coverage_query[:120],
    )

    try:
        candidates = await adapter.search(coverage_query, since_utc)
    except AdapterFetchError as exc:
        logger.warning(
            "coverage_report_gdelt_unavailable",
            story_id=story.canonical_story_id,
            coverage_mode=coverage_mode,
            error_type=type(exc).__name__,
            error_message=str(exc)[:300],
            fix_suggestion=(
                "GDELT census failed (rate-limit/HTTP/non-JSON); fell back to the "
                "story's clustered covering_outlets. Retry honors the <=1-req/5s throttle."
            ),
        )
        return _fallback_report(story, coverage_mode, outlets_lookup)

    distinct_domains = _distinct_coverage_domains(candidates)
    # Reason: an empty census (valid GDELT response, zero usable articles) is a
    # real "no broad coverage" signal — but we still seed from covering_outlets
    # so the tab never reads an implausible zero for a story we know is covered.
    if not distinct_domains:
        logger.info(
            "coverage_report_empty_census",
            story_id=story.canonical_story_id,
            coverage_mode=coverage_mode,
            candidates_returned=len(candidates),
            fix_suggestion="GDELT returned no usable outlets; using clustered covering_outlets",
        )
        return _fallback_report(story, coverage_mode, outlets_lookup)

    if coverage_mode == "partisan":
        report = _build_partisan_report(distinct_domains, outlets_lookup)
    else:
        report = _build_reach_report(candidates, distinct_domains)

    logger.info(
        "coverage_report_completed",
        story_id=story.canonical_story_id,
        coverage_mode=report.coverage_mode,
        coverage_outlet_count=report.coverage_outlet_count,
        blindspot_lean=report.blindspot_lean,
        coverage_momentum=report.coverage_momentum,
    )
    return report
