"""Source-tier authority table — the E1 ``authority`` term (FSR-M3 SP1).

The shared-pool E1 importance model (``reference/shared-pool-pipeline.md`` §4) weights a
story's outlet breadth by **source authority AND ideological diversity**: a story carried
by a few high-authority, ideologically-varied outlets is a *realer* story than one carried
by twenty content-farm reprints. This module is that authority signal — a config-driven
``outlet_authority(domain) -> tier_weight`` lookup plus the ``authority_and_diversity``
aggregator the per-cluster scorer consumes.

It is a **Python config artifact** (a ``domain -> tier`` map + a ``tier -> weight`` scale),
NOT a DB table — consistent with ``produce_gate``'s "single config source, no scattered
constants" convention, and because the authority of an outlet is editorial reference data,
not per-run state. The seed map below is a *representative* set (a handful of recognised
high-authority outlets across the ideological spectrum + a handful of known content farms),
enough for the offline DoD; a full outlet census is a content-ops tuning residual (PRD M3
Out-of-scope / Open items).

Everything here is **pure** (no DB, no clock, no network) — fully offline-unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable

# Reason: the authority tiers, highest → lowest. A small ordinal ladder (not a
# continuous score) keeps the seed map auditable — an editor curates a domain into a
# named band, and the numeric weight is set once here. ``TIER_WEIGHT`` is the single
# config source for the band → 0–1 multiplier mapping (no scattered constants).
TIER_PREMIER = "premier"  # global newspapers/wires of record (BBC, Reuters, AP, NYT, ...)
TIER_MAJOR = "major"  # established national/large outlets (Guardian, WSJ, Bloomberg, ...)
TIER_STANDARD = "standard"  # ordinary credible outlets — the default for unknown domains
TIER_CONTENT_FARM = "content_farm"  # low-authority aggregators / SEO reprint mills

# Reason: tier → 0–1 authority weight. Strictly decreasing so a premier outlet
# outweighs a content farm by construction (SP1 DoD (a)). The gap premier↔content_farm
# is wide on purpose: 20 content-farm reprints (0.15 each, but capped by diversity
# below) must not out-authority 3 premier outlets. Single config source.
TIER_WEIGHT: dict[str, float] = {
    TIER_PREMIER: 1.0,
    TIER_MAJOR: 0.75,
    TIER_STANDARD: 0.45,
    TIER_CONTENT_FARM: 0.15,
}

# Reason: the default tier for a domain absent from the seed map. An unknown outlet is
# treated as an ordinary credible outlet (STANDARD), NOT as a content farm (which would
# unfairly penalise every uncurated-but-legitimate source) and NOT as premier (which
# would let any unknown domain inflate authority). The seed map only needs to name the
# *exceptional* ends of the spectrum; the broad middle falls here.
DEFAULT_TIER: str = TIER_STANDARD

# Reason: the seed domain → tier map. A representative, ideologically-varied set of
# recognised high-authority outlets (so "diversity of authoritative outlets" is real,
# not a single newsroom's reprints) + a handful of known content farms. Domains are the
# bare registrable host (no scheme, no ``www.``) — callers normalise before lookup. This
# is a launch seed; a full census is a content-ops residual (PRD M3 Open items).
SOURCE_TIER_BY_DOMAIN: dict[str, str] = {
    # Premier — wires & papers of record, spanning regions/leanings.
    "reuters.com": TIER_PREMIER,
    "apnews.com": TIER_PREMIER,
    "bbc.com": TIER_PREMIER,
    "bbc.co.uk": TIER_PREMIER,
    "nytimes.com": TIER_PREMIER,
    "wsj.com": TIER_PREMIER,
    "ft.com": TIER_PREMIER,
    "economist.com": TIER_PREMIER,
    # Major — established national / large outlets, varied leanings.
    "theguardian.com": TIER_MAJOR,
    "washingtonpost.com": TIER_MAJOR,
    "bloomberg.com": TIER_MAJOR,
    "aljazeera.com": TIER_MAJOR,
    "cnn.com": TIER_MAJOR,
    "foxnews.com": TIER_MAJOR,
    "npr.org": TIER_MAJOR,
    "politico.com": TIER_MAJOR,
    # Content farms — low-authority SEO/aggregator/reprint mills (representative).
    "contentfarm.example": TIER_CONTENT_FARM,
    "newswirebot.example": TIER_CONTENT_FARM,
    "buzzaggregator.example": TIER_CONTENT_FARM,
    "clickfeed.example": TIER_CONTENT_FARM,
    "viralreprint.example": TIER_CONTENT_FARM,
}


def normalize_domain(domain: str | None) -> str:
    """Normalise an outlet domain to its bare registrable host for lookup.

    Strips scheme, a leading ``www.``, any path, and lowercases — so
    ``"https://www.BBC.com/news"`` and ``"bbc.com"`` resolve to the same tier. A
    ``None``/empty domain returns ``""`` (which falls to the default tier downstream).

    Args:
        domain: A raw outlet domain or URL fragment, possibly ``None``.

    Returns:
        The lowercased bare host (``""`` when the input is empty/``None``).

    Example:
        >>> normalize_domain("https://www.Reuters.com/world")
        'reuters.com'
        >>> normalize_domain(None)
        ''
    """
    if not domain:
        return ""
    host = domain.strip().lower()
    # Drop scheme if a full URL slipped through.
    if "://" in host:
        host = host.split("://", 1)[1]
    # Drop any path/query.
    host = host.split("/", 1)[0]
    # Drop a leading www.
    if host.startswith("www."):
        host = host[4:]
    return host


def outlet_tier(domain: str | None) -> str:
    """Return the authority TIER NAME for an outlet domain (default-safe).

    Looks the normalised domain up in :data:`SOURCE_TIER_BY_DOMAIN`; an unknown (or
    empty/``None``) domain falls to :data:`DEFAULT_TIER` — never crashes (SP1 DoD (b)).

    Args:
        domain: The outlet domain (raw or normalised; ``None`` allowed).

    Returns:
        One of the tier-name constants.

    Example:
        >>> outlet_tier("reuters.com")
        'premier'
        >>> outlet_tier("some-unknown-blog.example")
        'standard'
    """
    return SOURCE_TIER_BY_DOMAIN.get(normalize_domain(domain), DEFAULT_TIER)


def outlet_authority(domain: str | None) -> float:
    """Return the 0–1 authority WEIGHT for an outlet domain (default-safe).

    Resolves the domain's tier (:func:`outlet_tier`) and maps it to its
    :data:`TIER_WEIGHT`. A high-authority domain returns a strictly higher weight than a
    content-farm domain (SP1 DoD (a)); an unknown domain returns the default-tier weight.

    Args:
        domain: The outlet domain (raw or normalised; ``None`` allowed).

    Returns:
        The tier weight in ``[0, 1]``.

    Example:
        >>> outlet_authority("reuters.com") > outlet_authority("contentfarm.example")
        True
    """
    return TIER_WEIGHT[outlet_tier(domain)]


def authority_and_diversity(outlet_domains: Iterable[str | None]) -> float:
    """Aggregate a cluster's outlets into one 0–1 authority-and-diversity score.

    The E1 ``authority`` term (``reference/shared-pool-pipeline.md`` §4): "high-authority
    AND ideologically varied > 20 content farms". It must reward a *spread* of
    *authoritative* outlets, and must NOT let a pile of identical low-tier reprints win on
    raw volume. The construction:

      1. **Distinct outlets only** — dedupe on normalised domain, so N reprints from the
         same outlet contribute their authority ONCE (syndication can't fake diversity).
      2. **Sum of distinct-outlet authority weights** — a varied set of authoritative
         outlets accumulates; this is the "diversity" reward (3 distinct premier outlets
         sum to 3.0 of weight before scaling).
      3. **Diminishing returns via a soft cap** — divide the summed weight by
         (sum + ``_DIVERSITY_HALF_SAT``) so the score is in ``[0, 1)`` and saturates: the
         first few authoritative outlets move the score a lot, the 15th content-farm
         reprint barely moves it. This is what makes 3 premier outlets beat 10 content
         farms (SP1 DoD (c)): 3×1.0 summed saturates far higher than 10×0.15.

    A high-authority, varied set therefore scores strictly higher than a larger pile of
    low-tier/content-farm outlets — encoding the WHY (authority+diversity beats raw
    volume), the headline E1 property.

    Args:
        outlet_domains: The cluster's covering-outlet domains (raw; may repeat / be
            ``None``). Empty → 0.0.

    Returns:
        The authority-and-diversity score in ``[0, 1)``.

    Example:
        >>> three_premier = ["reuters.com", "apnews.com", "bbc.com"]
        >>> ten_farms = ["contentfarm.example"] * 10
        >>> authority_and_diversity(three_premier) > authority_and_diversity(ten_farms)
        True
    """
    distinct_hosts = {
        normalize_domain(domain) for domain in outlet_domains if normalize_domain(domain)
    }
    if not distinct_hosts:
        return 0.0
    total_authority = sum(outlet_authority(host) for host in distinct_hosts)
    # Soft saturation: monotone increasing in total_authority, asymptotes to 1.0.
    return total_authority / (total_authority + _DIVERSITY_HALF_SAT)


# Reason: the half-saturation constant for the diminishing-returns curve in
# ``authority_and_diversity``. At total authority == this value the score is 0.5; below
# it the curve is steep (early authoritative outlets matter a lot), above it flat (extra
# reprints barely move it). 2.0 means ~two premier outlets reach 0.5 and three reach
# 0.6 — comfortably above the ~0.43 a 10-content-farm pile (10×0.15=1.5) reaches. First
# draft; a tuning residual like the tier weights themselves.
_DIVERSITY_HALF_SAT: float = 2.0
