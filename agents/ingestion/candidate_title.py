r"""Title hygiene at GDELT admission — decode the entities, drop the masthead suffix.

The masthead/fragment gate (``agents/shared/headline_quality``, PRD decision 6) rejects
a title that IS a masthead. It has nothing to say about a title that merely CARRIES one,
or about a title GDELT handed over still HTML-escaped — so the 2026-07-25 shortlist put
``Abhishek Bachchan and Aishwarya Rai&#xA0;spotted…`` and
``… of controversial dialogues : Bollywood News`` in front of the founder
(``docs/ops/m1-live-audit-2026-07-25.md``, issue #71). The shortlist is a review surface
and dedup's publishable-title vote reads the same strings, so the dirt costs twice.

Two deterministic passes, no LLM and no API (founder credit-frugality rule):

  1. **Entity decode, exactly once.** ``html.unescape`` plus whitespace normalization —
     ``&#xA0;`` is a space, so words either side of one must come apart. Running the
     decode twice would turn a *literal* ``&amp;#x27;`` (what the publisher actually
     printed) into an apostrophe they never wrote, which is why this module owns the
     ONLY unescape on the admission path.
  2. **Trailing outlet-suffix strip, guarded.** A tail after a ``- – — | :`` seam is
     dropped when it is the OUTLET talking (its comparison key matches, or starts with,
     the outlet's own key — which also catches the fused
     ``… - FINCHANNELNASA Watchdogs Raise…`` double-headline) or when it has the shape
     of a site label (``: Bollywood News``, ``- Ipswich Town News``). Registry labels
     ("com", "net", "news") are excluded from matching entirely: a 3-letter prefix hit
     would truncate real headlines.

**The strip is bounded by the very gate this fixes.** A remainder is only accepted when
``is_publishable_headline`` still calls it publishable — the SAME classifier, never a
second one. There is no fallback title downstream (``reject_unpublishable_headline``
drops the story), so a dirty-but-publishable headline always beats a clean fragment; that
is why the Chinese ``…机遇- CFi.CN 中财网`` row keeps its suffix. Cleaning is
fail-safe throughout: it never returns an empty title for a non-empty input.

**Ordering against #63 is a contract, not a preference.** The caller runs
``evaluate_language_admission`` on the RAW title first and cleans afterwards. The
language gate decodes internally for detection, so cleaning first would stack two decodes
on a double-escaped title — and, worse, would delete the very outlet suffix
(``- CFi.CN 中财网``) that is sometimes the only non-Latin evidence in the row.

Pure module (no logging, no I/O), mirroring ``candidate_language``.

Example:
    >>> clean_candidate_title("Rai&#xA0;spotted in NYC : Bollywood News",
    ...                       outlet_domain="bollywoodhungama.com")
    'Rai spotted in NYC'
"""

from __future__ import annotations

import html
import re

from agents.shared.headline_quality import (
    comparison_key,
    is_publishable_headline,
    outlet_comparison_keys,
)

# Reason: a masthead seam is a dash/pipe/colon FOLLOWED by whitespace. Requiring the
# trailing space is what keeps "state-of-the-art" and "AC/DC" out; leading whitespace is
# optional because publishers emit both "… Final | Alice 107.7" and "…机遇- CFi.CN".
_SEPARATOR_RE = re.compile(r"\s*[-–—|:]\s+")

# Reason: a title can carry more than one appended tail ("Headline - Outlet | Network").
# Each pass strips at most one, so the loop is bounded rather than unbounded-while.
_MAX_SUFFIX_STRIPS = 3

# Reason: the prefix rule is the risky half — "News…" or "Netanyahu…" would match the
# registry/section labels that show up in hosts (news.yam.md, cfi.net.cn, twtd.co.uk)
# and silently truncate a real headline. These labels never identify an outlet on their
# own, so they are excluded from outlet matching, and every surviving key must be at
# least _MIN_OUTLET_KEY_LENGTH characters.
_GENERIC_DOMAIN_LABELS: frozenset[str] = frozenset(
    {"com", "net", "org", "www", "edition", "news", "info", "web", "online", "the"}
)
_MIN_OUTLET_KEY_LENGTH = 3

# Reason: a tail that merely BEGINS with the outlet key is the weaker signal (it is what
# catches the fused double-headline), so it needs a longer key than an exact match does.
# At three characters it would fire on ordinary prose — "Art of the deal…" on art.com,
# "One in five…" on one.com — while exact-match outlets that short (cnn.com, bbc.co.uk,
# npr.org) still work, because "… - CNN" matches the key outright.
_MIN_PREFIX_MATCH_KEY_LENGTH = 4

# Reason: outlets whose masthead the DOMAIN does not spell out ("Bollywood News" on
# bollywoodhungama.com, "Ipswich Town News" on twtd.co.uk) need a shape rule instead.
# It is deliberately narrow — a SHORT, fully capitalized tail whose last word is a site
# label — so "- and that's good news for renters" (lowercase, long) is never touched.
# Words that are ALSO ordinary headline nouns are deliberately absent: "today" ("- Best
# Deals Today"), "network" ("- Neural Network"), "media" ("- Social Media"), "wire" and
# "online" — each would truncate a real headline, and the outlets that use them
# ("USA Today", "Business Wire") are already caught by their own domain key. Extend on
# evidence only; the shortlist audit is what surfaces the next needed entry.
# fmt: off
_SITE_LABEL_WORDS: frozenset[str] = frozenset(
    {
        "news", "sport", "sports", "magazine", "times", "post", "daily", "weekly",
        "radio", "tv", "herald", "journal", "tribune", "gazette", "chronicle",
        "bulletin", "headlines", "press",
    }
)
# fmt: on
_MAX_SUFFIX_WORDS = 4


def _normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace (including a decoded ``&#xA0;``) to one space."""
    return " ".join(text.split())


def _matchable_outlet_keys(
    outlet_name: str | None, outlet_domain: str | None
) -> set[str]:
    """The outlet keys long and specific enough to match a suffix against."""
    return {
        key
        for key in outlet_comparison_keys(outlet_name, outlet_domain)
        if len(key) >= _MIN_OUTLET_KEY_LENGTH and key not in _GENERIC_DOMAIN_LABELS
    }


def _looks_like_site_label(suffix_text: str) -> bool:
    """True when the tail has the shape of a site label rather than a clause.

    Args:
        suffix_text: The text after the separator.

    Returns:
        Whether it is short, capitalized and ends in a site-label word.

    Example:
        >>> _looks_like_site_label("Ipswich Town News")
        True
        >>> _looks_like_site_label("what it means for renters")
        False
    """
    words = suffix_text.split()
    if not words or len(words) > _MAX_SUFFIX_WORDS:
        return False
    if comparison_key(words[-1]) not in _SITE_LABEL_WORDS:
        return False
    return all(word[0].isupper() for word in words if word[0].isalpha())


def _is_outlet_suffix(suffix_text: str, outlet_keys: set[str]) -> bool:
    """True when the tail is the outlet talking, not part of the headline."""
    suffix_key = comparison_key(suffix_text)
    if suffix_key and any(
        suffix_key == key
        or (len(key) >= _MIN_PREFIX_MATCH_KEY_LENGTH and suffix_key.startswith(key))
        for key in outlet_keys
    ):
        return True
    return _looks_like_site_label(suffix_text)


def _strip_one_outlet_suffix(
    title: str, outlet_name: str | None, outlet_domain: str | None
) -> str | None:
    """Strip the rightmost outlet suffix, or None when nothing qualifies.

    Separators are tried right-to-left so the last seam wins ("Maeda: Happy … - Ipswich
    Town News" loses the masthead, keeps the attribution colon).
    """
    outlet_keys = _matchable_outlet_keys(outlet_name, outlet_domain)
    for separator in reversed(list(_SEPARATOR_RE.finditer(title))):
        head = title[: separator.start()].strip()
        tail = title[separator.end() :].strip()
        if not head or not tail:
            continue
        if not _is_outlet_suffix(tail, outlet_keys):
            continue
        # Reason: the fragment guard. The remainder is published as-is if this story
        # survives, and persist has no fallback title — so a strip that leaves anything
        # the SAME classifier would reject is abandoned, dirt and all.
        if is_publishable_headline(head, outlet_name, outlet_domain):
            return head
    return None


def clean_candidate_title(
    candidate_title: str,
    outlet_name: str | None = None,
    outlet_domain: str | None = None,
) -> str:
    """Return the publishable form of a GDELT candidate title (pure, fail-safe).

    Decodes HTML entities exactly once, normalizes whitespace, then strips a trailing
    outlet suffix when — and only when — the remainder is still a publishable headline.

    Args:
        candidate_title: The title exactly as GDELT delivered it.
        outlet_name: The outlet's display name, when known.
        outlet_domain: The outlet's domain, when known.

    Returns:
        The cleaned title; the original (stripped) input when cleaning would empty it.

    Example:
        >>> clean_candidate_title(
        ...     "UN debate puts spotlight on critical minerals governance "
        ...     "&#x2013; Channel Africa",
        ...     outlet_domain="channelafrica.co.za",
        ... )
        'UN debate puts spotlight on critical minerals governance'
    """
    original = (candidate_title or "").strip()
    cleaned = _normalize_whitespace(html.unescape(original))
    for _ in range(_MAX_SUFFIX_STRIPS):
        stripped = _strip_one_outlet_suffix(cleaned, outlet_name, outlet_domain)
        if stripped is None:
            break
        cleaned = stripped
    # Reason: a title made of nothing but entity whitespace ("&#xA0;") decodes to the
    # empty string. An empty title is not an improvement over a dirty one — and the
    # adapter has already accepted this row on the strength of a non-empty title.
    return cleaned or original
