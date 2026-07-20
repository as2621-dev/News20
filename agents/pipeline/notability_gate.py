"""Notability gate (feed-quality reset WS-B, PRD RC2 / decision 4) — is a candidate news?

Before the pipeline spends anything producing a story, it must be **plausibly news**
(PRD "root cause" RC2). Admission used to be *any single anchor term matching the GDELT
haystack*, so a one-outlet local notice or a PR-wire release reached the top-30. This
module is the hard cut that closes that gap. A candidate is notable when EITHER:

  - **≥2 distinct EDITORIAL outlets** cover it — one newsroom's word is a *claim*, two
    independent newsrooms is *news*; OR
  - a covering outlet is **authority-tier** (``premier``/``major`` in
    ``agents.pipeline.importance.source_tiers``) — a single Reuters/AP/BBC story is news.

**Corroboration ≠ syndication (the headline property).** Distinctness is real: N reprints
of one PR-wire item across a dozen press-release distributors are ONE press release, not a
dozen outlets. The corroborating-outlet count therefore drops non-editorial domains
(PR wires, social/aggregator silos) and content-farm-tier outlets before counting, so a
syndication burst collapses to zero corroborating outlets and FAILS.

**Thin-niche relaxation (decision 4).** Tightening admission must not *starve* a legitimately
thin niche (e.g. AI-interpretability) into a permanent "Beyond your bubble". When a leaf
niche has fewer than ``_THIN_NICHE_SURVIVOR_FLOOR`` strict survivors it drops to a *relaxed*
rule (≥1 distinct editorial outlet), and every relaxed survivor is STAMPED
``notability_rung="relaxed"`` so ``feed_assembly``'s honesty ladder can surface it — never a
silent pad. A pure syndication burst (zero editorial outlets) fails even under relaxation.

**Fail loud (Rule 12).** If the authority-classification config is unavailable the gate
raises :class:`NotabilityConfigError` rather than silently passing everything — a disabled
notability gate is exactly the RC2 bug. Every batch also emits candidates-surviving-per-niche
as structured JSON, so an over-tight gate that starves niches is visible on the first M2 run.

Thresholds are **module constants** (no config surface, single source), each with a
``# Reason:`` comment. Every function is **pure** over its injected inputs — the gate reads
the ``source_tiers`` config and the ``CanonicalStory`` outlet fields, with no DB, clock, or
network — so it is fully offline-unit-testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from agents.ingestion.dedup import is_source_origin_domain
from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.importance import source_tiers
from agents.shared.logger import get_logger

logger = get_logger("pipeline.notability_gate")

# Reason: strict corroboration floor. A story is "plausibly news" when at least this many
# DISTINCT editorial outlets cover it. 2 encodes corroboration: one newsroom is a claim,
# two independent newsrooms is news (PRD RC2). Single config source — no scattered constant.
_MIN_DISTINCT_OUTLETS_STRICT: int = 2

# Reason: the relaxed corroboration floor for a THIN niche (decision 4). A leaf niche with
# too few strict survivors drops to this rather than starving into permanent "Beyond your
# bubble" — but it is NEVER 0: a candidate still needs ≥1 distinct EDITORIAL outlet, so a
# pure PR-wire/syndication burst (0 editorial outlets) fails even under relaxation.
_MIN_DISTINCT_OUTLETS_RELAXED: int = 1

# Reason: the strict-survivor count below which a leaf niche is "thin" and invokes the
# relaxed rule. At/above it the niche has enough real news to stay strict. First draft,
# tuned against the M2 "candidates-surviving-per-niche" acceptance audit (a residual).
_THIN_NICHE_SURVIVOR_FLOOR: int = 3

# Reason: which source-tier bands count as an "authority outlet" for the OR branch.
# PREMIER (wires/papers of record) and MAJOR (established nationals) are authoritative
# enough that ONE alone clears notability. STANDARD (the default for UNKNOWN domains)
# deliberately does NOT count — if it did, every single-outlet unknown-domain story would
# pass and the gate would be a no-op (the RC2 bug it exists to fix).
_AUTHORITY_TIERS: frozenset[str] = frozenset(
    {source_tiers.TIER_PREMIER, source_tiers.TIER_MAJOR}
)

# Reason: non-editorial domains that never corroborate — PR-wire / press-release
# distributors and social/aggregator silos. N reprints of ONE press release across a dozen
# of these is SYNDICATION, not independent journalism (PRD: "corroboration ≠ syndication").
# Mirrors the intent of ``stages.coverage_gdelt._NOISE_DOMAINS`` but kept as a local
# constant so the gate has one auditable config source (no import of a census-internal set).
# Bare-host lowercase — the form ``source_tiers.normalize_domain`` emits.
_NON_EDITORIAL_DOMAINS: frozenset[str] = frozenset(
    {
        # PR-wire / press-release distributors — a wire release is a single source.
        "prnewswire.com",
        "globenewswire.com",
        "businesswire.com",
        "prweb.com",
        "einnews.com",
        "newswire.com",
        "accesswire.com",
        "prlog.org",
        "24-7pressrelease.com",
        "openpr.com",
        "pressreleasepoint.com",
        "issuewire.com",
        # Social / aggregator silos — a link on these is not an editorial voice.
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
    }
)

# Notability rungs — stamped on each surviving slot so the honesty ladder can surface HOW a
# candidate qualified (feed_assembly reads these alongside its leaf/climbed/beyond rungs).
NOTABILITY_RUNG_STRICT: str = "strict"
NOTABILITY_RUNG_RELAXED: str = "relaxed"
NOTABILITY_RUNG_SOURCE_ORIGIN: str = "source_origin"  # followed YouTube/X — gate-exempt

# Machine-readable fail reason (so the batch log/metrics can bucket rejects).
FAIL_REASON_INSUFFICIENT_OUTLETS: str = "insufficient_distinct_or_authority_outlets"


class NotabilityConfigError(RuntimeError):
    """The authority-classification config is unavailable — the gate must fail LOUD.

    Raised instead of silently passing every candidate when the ``source_tiers`` authority
    map cannot classify authority outlets. A disabled notability gate is the RC2 bug, so the
    batch aborts loudly rather than admitting one-outlet junk (Rule 12).
    """


class NotabilityDecision(BaseModel):
    """The notability gate's typed verdict for one canonical story.

    Attributes:
        story_id: The canonical story this verdict is for.
        is_notable: True when the story cleared the gate (strict, relaxed, or source-origin).
        notability_rung: HOW it qualified — ``strict`` / ``relaxed`` / ``source_origin`` when
            notable, else ``""``. ``relaxed`` marks a thin-niche admission for the honesty ladder.
        distinct_outlet_count: Distinct CORROBORATING (editorial, non-syndication) outlets.
        has_authority_outlet: True when a covering outlet is authority-tier.
        leaf_niche_ids: The leaf interest ids this story serves (its niches for relaxation).
        fail_reason: Machine-readable reason when not notable, else ``""``.
    """

    story_id: str = Field(..., description="The canonical story this verdict is for")
    is_notable: bool = Field(..., description="True when the story cleared the gate")
    notability_rung: str = Field(
        default="", description="strict / relaxed / source_origin when notable, else ''"
    )
    distinct_outlet_count: int = Field(
        default=0, ge=0, description="Distinct corroborating (editorial) outlet count"
    )
    has_authority_outlet: bool = Field(
        default=False, description="True when a covering outlet is authority-tier"
    )
    leaf_niche_ids: list[str] = Field(
        default_factory=list, description="Leaf interest ids the story serves"
    )
    fail_reason: str = Field(
        default="", description="Machine-readable reason when not notable, else ''"
    )


class NotabilityBatchResult(BaseModel):
    """The notability gate's batch verdict over a whole candidate pool.

    Attributes:
        notable_story_ids: Ids of the stories that cleared the gate (in input order).
        decisions: Per-story :class:`NotabilityDecision` for the full pool.
        surviving_by_niche: ``{leaf_niche_id: surviving count}`` (strict + relaxed) — the
            candidates-surviving-per-niche the M2 audit reads (PRD de-risking).
        strict_survivors_by_niche: ``{leaf_niche_id: strict-only survivor count}`` — so the
            audit can tell a healthy niche from one propped up by relaxation.
        relaxed_niche_ids: Leaf niches that were thin and invoked the relaxed rule (sorted).
    """

    notable_story_ids: list[str] = Field(default_factory=list)
    decisions: list[NotabilityDecision] = Field(default_factory=list)
    surviving_by_niche: dict[str, int] = Field(default_factory=dict)
    strict_survivors_by_niche: dict[str, int] = Field(default_factory=dict)
    relaxed_niche_ids: list[str] = Field(default_factory=list)


def _outlet_hosts(covering_outlets: Iterable[str | None]) -> set[str]:
    """Normalize covering-outlet domains to their distinct bare hosts (drops empties)."""
    return {
        source_tiers.normalize_domain(domain)
        for domain in covering_outlets
        if source_tiers.normalize_domain(domain)
    }


def count_distinct_corroborating_outlets(
    covering_outlets: Iterable[str | None],
) -> int:
    """Count DISTINCT corroborating editorial outlets covering a story.

    The syndication-dampened distinctness at the heart of "corroboration ≠ syndication":
    normalizes each outlet to its bare host (so regional editions / casing / ``www.`` /
    full URLs collapse to one), then DROPS domains that do not independently corroborate —
    PR-wire / press-release distributors and social/aggregator silos
    (:data:`_NON_EDITORIAL_DOMAINS`), plus content-farm-tier outlets
    (``source_tiers`` ``content_farm``). What remains is the count of independent editorial
    voices. A dozen reprints of one wire item therefore count as zero.

    Args:
        covering_outlets: The story's covering-outlet domains (raw; may repeat / be None).

    Returns:
        The number of distinct corroborating editorial outlets (``>= 0``).

    Example:
        >>> count_distinct_corroborating_outlets(["denverpost.com", "sfchronicle.com"])
        2
        >>> count_distinct_corroborating_outlets(["prnewswire.com", "businesswire.com"])
        0
    """
    corroborating = {
        host
        for host in _outlet_hosts(covering_outlets)
        if host not in _NON_EDITORIAL_DOMAINS
        and source_tiers.outlet_tier(host) != source_tiers.TIER_CONTENT_FARM
    }
    return len(corroborating)


def has_authority_outlet(covering_outlets: Iterable[str | None]) -> bool:
    """Return True when any covering outlet is authority-tier (premier/major).

    A single authority outlet clears notability alone (the OR branch). STANDARD-tier
    (the default for unknown domains) does NOT count — see :data:`_AUTHORITY_TIERS`.

    Args:
        covering_outlets: The story's covering-outlet domains (raw; may repeat / be None).

    Returns:
        True if at least one distinct covering outlet resolves to an authority tier.

    Example:
        >>> has_authority_outlet(["reuters.com"])
        True
        >>> has_authority_outlet(["some-unknown-blog.example"])
        False
    """
    return any(
        source_tiers.outlet_tier(host) in _AUTHORITY_TIERS
        for host in _outlet_hosts(covering_outlets)
    )


def is_notable(
    covering_outlets: Iterable[str | None],
    *,
    min_distinct_outlets: int = _MIN_DISTINCT_OUTLETS_STRICT,
) -> bool:
    """Decide whether a story's outlets clear the notability rule.

    The rule is ``(distinct corroborating outlets >= min_distinct_outlets) OR authority``.
    Pass :data:`_MIN_DISTINCT_OUTLETS_STRICT` (default) for the strict rule, or
    :data:`_MIN_DISTINCT_OUTLETS_RELAXED` for the thin-niche relaxed rule.

    Args:
        covering_outlets: The story's covering-outlet domains.
        min_distinct_outlets: The distinct-corroborating-outlet floor to apply.

    Returns:
        True when the story is notable under the given floor.

    Example:
        >>> is_notable(["denverpost.com", "sfchronicle.com"])
        True
        >>> is_notable(["denverpost.com"])
        False
        >>> is_notable(["denverpost.com"], min_distinct_outlets=1)
        True
    """
    outlets = list(covering_outlets)
    if has_authority_outlet(outlets):
        return True
    return count_distinct_corroborating_outlets(outlets) >= min_distinct_outlets


def _require_authority_config() -> None:
    """Assert the authority-classification config is available, else fail LOUD.

    The gate identifies authority outlets via ``source_tiers`` — if that map is empty the
    gate cannot classify authority outlets, and continuing would silently degrade to
    "corroboration only" or (worse, if a caller mis-wired a default) an open gate. Rather
    than risk re-opening the RC2 hole, abort the batch loudly (Rule 12).

    Raises:
        NotabilityConfigError: When the authority tier map is empty/unavailable.
    """
    tier_map = source_tiers.SOURCE_TIER_BY_DOMAIN
    has_authority_domain = any(tier in _AUTHORITY_TIERS for tier in tier_map.values())
    if not tier_map or not has_authority_domain:
        logger.error(
            "notability_authority_config_unavailable",
            tier_map_size=len(tier_map),
            fix_suggestion=(
                "The source_tiers authority map is empty or names no premier/major "
                "outlet, so the notability gate cannot identify authority outlets. The "
                "batch aborted rather than silently admitting one-outlet junk. Restore "
                "agents/pipeline/importance/source_tiers.SOURCE_TIER_BY_DOMAIN."
            ),
        )
        raise NotabilityConfigError(
            "authority tier config unavailable — notability gate cannot run"
        )


def _leaf_niches_by_story(
    story_interest_tags: Iterable[StoryInterestTag],
) -> dict[str, set[str]]:
    """Map each story to the set of leaf interest ids it serves (its niches).

    A "niche" for relaxation is a leaf interest (``story_interest_match_depth == 0``); a
    story matched only at an ancestor depth is not counted as belonging to that leaf niche.
    """
    by_story: dict[str, set[str]] = defaultdict(set)
    for tag in story_interest_tags:
        if tag.story_interest_match_depth == 0:
            by_story[tag.story_interest_story_id].add(tag.story_interest_interest_id)
    return by_story


def apply_notability_gate(
    stories: Sequence[CanonicalStory],
    story_interest_tags: Iterable[StoryInterestTag],
) -> NotabilityBatchResult:
    """Apply the notability gate across a candidate pool, with thin-niche relaxation.

    The end-to-end batch cut wired at the produce/rank seam. For each story it computes the
    distinct-corroborating-outlet count and authority flag, applies the STRICT rule, then —
    per leaf niche — relaxes to the single-outlet rule when the niche has fewer than
    :data:`_THIN_NICHE_SURVIVOR_FLOOR` strict survivors, STAMPING each relaxed survivor so the
    honesty ladder can surface it. Followed-source (YouTube/X) stories BYPASS the gate
    entirely (parity with ``produce_gate``'s source-origin exemption — they are intrinsically
    wanted and single-source by nature). Emits candidates-surviving-per-niche as structured
    JSON for the M2 starvation audit.

    Args:
        stories: The deduped canonical story pool (post-reconcile).
        story_interest_tags: All ``story_interests`` tag payloads for the pool (leaf tags
            define each story's niches for relaxation).

    Returns:
        A :class:`NotabilityBatchResult` — the notable ids, per-story decisions, and the
        per-niche surviving counts.

    Raises:
        NotabilityConfigError: When the authority config is unavailable (fail loud).
    """
    _require_authority_config()

    leaf_niches = _leaf_niches_by_story(story_interest_tags)

    # --- Pass 1: per-story primitives + strict verdict ---
    outlets_by_story: dict[str, list[str | None]] = {}
    distinct_by_story: dict[str, int] = {}
    authority_by_story: dict[str, bool] = {}
    source_origin_ids: set[str] = set()
    strict_pass_ids: set[str] = set()

    for story in stories:
        story_id = story.canonical_story_id
        # Fall back to the primary outlet when no covering set was populated, so a
        # single-primary-outlet story is still evaluated (never crashes on an empty list).
        outlets = story.covering_outlets or [story.canonical_primary_outlet_domain]
        outlets_by_story[story_id] = outlets
        distinct_by_story[story_id] = count_distinct_corroborating_outlets(outlets)
        authority_by_story[story_id] = has_authority_outlet(outlets)

        if is_source_origin_domain(story.canonical_primary_outlet_domain):
            source_origin_ids.add(story_id)
        elif (
            authority_by_story[story_id]
            or distinct_by_story[story_id] >= _MIN_DISTINCT_OUTLETS_STRICT
        ):
            strict_pass_ids.add(story_id)

    # --- Pass 2: per-niche strict-survivor census → which niches are thin ---
    strict_survivors_by_niche: dict[str, int] = defaultdict(int)
    all_niche_members: dict[str, list[str]] = defaultdict(list)
    for story in stories:
        story_id = story.canonical_story_id
        for niche_id in leaf_niches.get(story_id, set()):
            all_niche_members[niche_id].append(story_id)
            if story_id in strict_pass_ids:
                strict_survivors_by_niche[niche_id] += 1

    thin_niches = {
        niche_id
        for niche_id, members in all_niche_members.items()
        if strict_survivors_by_niche[niche_id] < _THIN_NICHE_SURVIVOR_FLOOR
    }

    # --- Pass 3: relaxation for thin niches, then stamp each story's rung ---
    relaxed_pass_ids: set[str] = set()
    for story_id in outlets_by_story:
        if story_id in strict_pass_ids or story_id in source_origin_ids:
            continue
        story_niches = leaf_niches.get(story_id, set())
        # Relax only when the story belongs to a THIN niche AND clears the relaxed floor.
        in_thin_niche = any(niche_id in thin_niches for niche_id in story_niches)
        if in_thin_niche and is_notable(
            outlets_by_story[story_id],
            min_distinct_outlets=_MIN_DISTINCT_OUTLETS_RELAXED,
        ):
            relaxed_pass_ids.add(story_id)

    decisions: list[NotabilityDecision] = []
    notable_story_ids: list[str] = []
    for story in stories:
        story_id = story.canonical_story_id
        if story_id in source_origin_ids:
            rung, notable = NOTABILITY_RUNG_SOURCE_ORIGIN, True
        elif story_id in strict_pass_ids:
            rung, notable = NOTABILITY_RUNG_STRICT, True
        elif story_id in relaxed_pass_ids:
            rung, notable = NOTABILITY_RUNG_RELAXED, True
        else:
            rung, notable = "", False

        if notable:
            notable_story_ids.append(story_id)
        decisions.append(
            NotabilityDecision(
                story_id=story_id,
                is_notable=notable,
                notability_rung=rung,
                distinct_outlet_count=distinct_by_story[story_id],
                has_authority_outlet=authority_by_story[story_id],
                leaf_niche_ids=sorted(leaf_niches.get(story_id, set())),
                fail_reason="" if notable else FAIL_REASON_INSUFFICIENT_OUTLETS,
            )
        )

    # --- Per-niche surviving census (strict + relaxed) for the M2 starvation audit ---
    notable_id_set = set(notable_story_ids)
    surviving_by_niche: dict[str, int] = {
        niche_id: sum(1 for sid in members if sid in notable_id_set)
        for niche_id, members in all_niche_members.items()
    }
    relaxed_niche_ids = sorted(
        niche_id
        for niche_id in thin_niches
        if any(sid in relaxed_pass_ids for sid in all_niche_members[niche_id])
    )

    logger.info(
        "notability_gate_batch_completed",
        total_stories=len(stories),
        notable=len(notable_story_ids),
        rejected=len(stories) - len(notable_story_ids),
        strict=len(strict_pass_ids),
        relaxed=len(relaxed_pass_ids),
        source_origin=len(source_origin_ids),
        thin_niche_count=len(thin_niches),
        surviving_by_niche=dict(sorted(surviving_by_niche.items())),
        strict_survivors_by_niche=dict(sorted(strict_survivors_by_niche.items())),
        relaxed_niche_ids=relaxed_niche_ids,
        fix_suggestion=(
            "If a niche shows 0 survivors it is starving — widen the thin-niche floor or "
            "the curated authority-domain set, do NOT disable the gate (PRD decision 4)."
        ),
    )

    return NotabilityBatchResult(
        notable_story_ids=notable_story_ids,
        decisions=decisions,
        surviving_by_niche=surviving_by_niche,
        strict_survivors_by_niche=dict(strict_survivors_by_niche),
        relaxed_niche_ids=relaxed_niche_ids,
    )
