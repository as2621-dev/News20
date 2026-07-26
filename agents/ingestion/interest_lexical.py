r"""The lexical relevance key — phrase-level anchors + per-interest term hygiene.

This is the **lexical half** of the two-key relevance lock (PRD decision 5, RC3):
a story may be admitted for / tagged with an interest only when it clears
phrase-level anchor matching, with per-interest term hygiene. The **semantic
half** (embedding similarity between story and interest) is a follow-on slice
(#51) that sits beside this module.

Why this exists (RC3, the founder's 07-07 + 07-19 briefings). Admission used to
be "ANY single anchor *word* regex-matches the GDELT title/entity haystack", which
let generic single words admit and mis-tag junk: "Foundation" pulled a venison
donation into AI interests, "Federal" pulled Hawaii funding into interest-rates,
"India" pulled an Indonesia-port story into cricket, and a "data center" bag-of-
words matched a zoning lawsuit. This module closes the lexical class of that bug
with three rules:

  1. **Phrases match as phrases, not bags of words.** ``interest_search_query`` is
     a comma-joined list of anchor phrases (the convention every seeder/backfill
     writes — see ``scripts/seed_catalog/backfill_interest_query.py`` and
     ``src/lib/interviewProfile.ts``; anchors persist ``distinctNonEmpty(...).join(", ")``).
     Each comma phrase becomes ONE anchor whose regex requires its tokens to appear
     **contiguously** (``\bdata\s+center\b``), so "data" and "center" occurring far
     apart no longer admit.
  2. **Banned standalone generic anchors can never admit alone.** A single-word
     anchor equal to a content-free institutional word ("foundation", "federal",
     "india", "trust", …) is dropped entirely. The same word *inside* a multi-word
     phrase ("foundation models") is kept — the phrase is specific.
  3. **Short / ambiguous anchors additionally require a TITLE match.** A tiny single
     word ("fed", "ipl") only counts when it appears in the story **title**, not
     merely in the entity haystack — a title hit means the story is *about* it, an
     entity-tag hit is incidental.

**Twin note (SQL ⇄ Python).** The runtime admission gate is the ``_BATCH_SQL``
query in ``gdelt_bigquery.py``: it ``JOIN``s the anchor structs this module builds
(regex + ``requires_title``) and evaluates exactly
``REGEXP_CONTAINS(hay, term) AND (NOT requires_title OR REGEXP_CONTAINS(title_hay, term))``.
:func:`evaluate_anchor_match` is the pure-Python mirror of that predicate — the
behavioural specification the SQL is built to satisfy, exercised directly in tests
(BigQuery can't run offline), and importable by any caller (the semantic gate #51,
the DOC path) that has the haystack in Python. Both sides consume the SAME
:func:`derive_anchor_specs` output, so they cannot drift on which anchors exist or
what regex each is; only the boolean predicate is mirrored, and it is pinned by a
test that asserts the SQL predicate string.

This module is **pure** (no logging, no I/O) so it is fully unit-testable; the
caller (``gdelt_bigquery.search_active_interests``) owns the term-hygiene logging.

Example:
    >>> d = derive_anchor_specs("artificial intelligence, foundation, ipl")
    >>> [s.anchor_phrase for s in d.specs]
    ['artificial intelligence', 'ipl']
    >>> d.banned_anchors
    ['foundation']
    >>> evaluate_anchor_match("openai ships a new model", "openai ships a new model "
    ...                       "artificial intelligence lab", d.specs)
    True
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# Reason: reuse the recall path's tokenizer + stopword/event-word sets verbatim so
# the lexical key and the recall path can never disagree on what a "token" is or
# which fillers to drop (single source of truth; the import is one-directional —
# gdelt_bigquery pulls derive_anchor_specs back in via a function-local import).
from agents.ingestion.adapters.gdelt_bigquery import (
    _GENERIC_BROAD,
    _QUERY_STOPWORDS,
)

# Reason: institutional / geographic words that carry no topical signal ALONE — each
# was (or is the same class as) a live RC3 false positive: "foundation"→AI (venison),
# "federal"→interest-rates (Hawaii), "india"→cricket (Indonesia port). A single-word
# anchor equal to one of these can NEVER admit; the same word inside a multi-word
# phrase ("russian federation", "foundation models") is specific and kept. Kept tight
# and evidence-driven — over-banning would violate acceptance criterion 4 (a real
# short interest that IS the subject must still pass). Extend only on new evidence;
# the ``interest_term_hygiene_gap`` log surfaces interests that need a new entry.
_BANNED_STANDALONE_ANCHORS: frozenset[str] = frozenset(
    {
        "foundation",
        "federal",
        "india",
        "trust",
        "national",
        "federation",
        "union",
        "authority",
        "ministry",
        "development",
        "council",
    }
)

# Reason: a single-word anchor this short (in chars) is ambiguous — an acronym or
# tiny token ("fed", "ipl", "oil") collides across topics, so it must appear in the
# TITLE (story is *about* it) rather than merely an entity tag. Multi-word phrases are
# never "short" (contiguity already makes them specific). Tokens < 3 chars are dropped
# entirely upstream (the tokenizer floor), so in practice this gates 3-char anchors.
_SHORT_ANCHOR_MAX_LEN = 3

# Reason (issue #69): a query with NO comma but this many words is keyword *soup*, not
# an anchor phrase — the 2026-07-04 catalog seed wrote space-joined lists ("humanoid
# robots robotics news") where the contract wants "humanoid robots, robotics". Soup
# derives ONE anchor demanding the whole sentence contiguously, so the interest matches
# nothing while looking healthy (it has a spec, and a strong one). 4 is the floor that
# spares a genuine 3-word phrase ("Supreme Court ruling") — see the boundary test.
_SOUP_QUERY_MIN_WORDS = 4


@dataclass(frozen=True)
class AnchorSpec:
    """One admitting anchor derived from an interest's ``interest_search_query``.

    Attributes:
        anchor_phrase: The normalized phrase — lowercased tokens joined by single
            spaces (``"data center"``). Human-readable; also the dedup key.
        anchor_regex: The RE2/``re`` pattern the SQL and :func:`evaluate_anchor_match`
            evaluate — word-boundary-anchored, tokens joined by ``\\s+`` so they must
            appear contiguously (``\\bdata\\s+center\\b``).
        requires_title: When True the anchor only counts on a **title** match, not a
            bare haystack match (short/ambiguous single-word anchors).
    """

    anchor_phrase: str
    anchor_regex: str
    requires_title: bool


@dataclass
class AnchorDerivation:
    """The anchors an interest yields, plus the anchors dropped for term hygiene.

    Attributes:
        specs: The usable admitting anchors (banned standalones already removed).
        banned_anchors: Single-word anchors dropped because they are banned
            standalone generics — surfaced so the term-hygiene gap is visible
            (never a silent "this interest cannot match" — Rule 12).
        source_query: The verbatim ``interest_search_query`` these anchors came from,
            kept so the derivation can diagnose the *shape* of the query itself (see
            :attr:`has_uncommaed_soup_query`) and not just its anchors.
    """

    specs: list[AnchorSpec] = field(default_factory=list)
    banned_anchors: list[str] = field(default_factory=list)
    source_query: str = ""

    @property
    def has_hygiene_gap(self) -> bool:
        """True when the interest has anchors but NONE is a strong (admit-alone) one.

        A "strong" anchor is usable AND not ``requires_title`` — it can admit a story
        via the entity haystack. When every derived anchor is either banned (dropped)
        or short/ambiguous (title-only), the interest is fragile: it can match nothing
        on the body/entities and, if every anchor was banned, nothing at all. That is
        a term-hygiene gap the caller must log (acceptance criterion 5). An interest
        with no anchors at all (empty / all-stopword query) is NOT a hygiene gap —
        that is the separate "no usable terms" skip.
        """
        any_anchor = bool(self.specs) or bool(self.banned_anchors)
        strong_exists = any(not spec.requires_title for spec in self.specs)
        return any_anchor and not strong_exists

    @property
    def has_uncommaed_soup_query(self) -> bool:
        """True when the query is keyword SOUP: zero commas and >= 4 words.

        The contract is a comma-joined list of anchor phrases, so a comma-less query
        of four or more words is almost certainly a keyword list the author forgot to
        comma-separate — and it derives ONE anchor requiring all of those words to
        appear contiguously, which effectively never happens. This is invisible to
        :attr:`has_hygiene_gap` (the mega-anchor is multi-word, hence "strong"), which
        is exactly why the 2026-07-25 run returned zero rows for every interest without
        a single warning. Words are counted RAW (fillers included): a filler like
        "news" is dropped from the anchor but is still evidence the author was listing
        keywords, and dropping it first would hide 'hurricane tropical storm news'.
        """
        if "," in self.source_query:
            return False
        return len(re.findall(r"[a-z0-9]+", self.source_query.lower())) >= _SOUP_QUERY_MIN_WORDS


def _phrase_tokens(raw_phrase: str) -> list[str]:
    """Lowercase alphanumeric tokens (>= 3 chars, non-stopword) of one comma phrase.

    Mirrors the recall tokenizer's floor (``>= 3`` chars) and drops ``_QUERY_STOPWORDS``
    so fillers ("news", "the") never become part of a phrase. Event/generic-broad words
    are KEPT here (they are dropped only when they would stand ALONE — see
    :func:`derive_anchor_specs`) so a specific phrase like "trade war" survives intact.
    """
    return [
        token
        for token in re.findall(r"[a-z0-9]+", raw_phrase.lower())
        if len(token) >= 3 and token not in _QUERY_STOPWORDS
    ]


def _anchor_regex(tokens: list[str]) -> str:
    r"""Build the contiguous-phrase pattern for a token list.

    ``["data", "center"]`` -> ``\bdata\s+center\b`` (tokens must be adjacent, any
    whitespace between). A single token -> ``\btoken\b`` (identical to the recall
    path's ``_term_regex`` so the two paths share pattern shape).
    """
    return r"\b" + r"\s+".join(re.escape(token) for token in tokens) + r"\b"


def derive_anchor_specs(search_query: str) -> AnchorDerivation:
    """Derive phrase-level admitting anchors from an ``interest_search_query``.

    The query is a comma-joined list of anchor phrases; each comma phrase becomes at
    most one :class:`AnchorSpec`. A phrase is dropped when it tokenizes to nothing.
    A single-token phrase equal to a banned standalone generic is dropped and recorded
    in ``banned_anchors``. A single-token phrase is marked ``requires_title`` when it
    is short (<= :data:`_SHORT_ANCHOR_MAX_LEN` chars) or a content-free event word
    (``_GENERIC_BROAD``). Multi-token phrases are always kept and never title-gated.
    Specs are de-duplicated on ``anchor_phrase`` preserving first-seen order.

    Args:
        search_query: The interest's free-text, comma-joined query (may be empty).

    Returns:
        An :class:`AnchorDerivation` with the usable specs and the banned anchors.

    Example:
        >>> d = derive_anchor_specs("data center buildout, foundation, fed")
        >>> [(s.anchor_phrase, s.requires_title) for s in d.specs]
        [('data center buildout', False), ('fed', True)]
        >>> d.banned_anchors
        ['foundation']
    """
    specs: list[AnchorSpec] = []
    banned_anchors: list[str] = []
    seen_phrases: set[str] = set()

    for raw_phrase in search_query.split(","):
        tokens = _phrase_tokens(raw_phrase)
        if not tokens:
            continue

        if len(tokens) == 1:
            token = tokens[0]
            if token in _BANNED_STANDALONE_ANCHORS:
                if token not in banned_anchors:
                    banned_anchors.append(token)
                continue
            requires_title = (
                len(token) <= _SHORT_ANCHOR_MAX_LEN or token in _GENERIC_BROAD
            )
        else:
            requires_title = False

        anchor_phrase = " ".join(tokens)
        if anchor_phrase in seen_phrases:
            continue
        seen_phrases.add(anchor_phrase)
        specs.append(
            AnchorSpec(
                anchor_phrase=anchor_phrase,
                anchor_regex=_anchor_regex(tokens),
                requires_title=requires_title,
            )
        )

    return AnchorDerivation(
        specs=specs, banned_anchors=banned_anchors, source_query=search_query
    )


def evaluate_anchor_match(
    title_hay: str, hay: str, specs: Iterable[AnchorSpec]
) -> bool:
    """Does a story clear the lexical key for an interest's anchors?

    The pure-Python mirror of the ``_BATCH_SQL`` admission predicate: a story matches
    when ANY anchor matches the haystack AND (if that anchor ``requires_title``) also
    matches the title. ``title_hay`` and ``hay`` must be lowercased by the caller
    (the SQL lowercases via ``LOWER(...)``); ``hay`` is the title + entity haystack,
    ``title_hay`` the title alone.

    Args:
        title_hay: The lowercased story title.
        hay: The lowercased title + persons/orgs/locations haystack.
        specs: The interest's anchors (from :func:`derive_anchor_specs`).

    Returns:
        True if the story is admitted for the interest under the lexical key.

    Example:
        >>> specs = derive_anchor_specs("fed").specs
        >>> evaluate_anchor_match("fed cup tennis", "fed cup tennis wta", specs)
        True
        >>> # "fed" only in the entity haystack, not the title -> rejected
        >>> evaluate_anchor_match("wta tour final", "wta tour final fed cup", specs)
        False
    """
    for spec in specs:
        if re.search(spec.anchor_regex, hay) and (
            not spec.requires_title or re.search(spec.anchor_regex, title_hay)
        ):
            return True
    return False
