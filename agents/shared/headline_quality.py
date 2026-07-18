"""Headline quality: is this string a headline, or the site it came from?

GDELT's ``<PAGE_TITLE>`` is whatever the publisher put in the ``<title>`` tag — for
many small outlets that is the masthead ("Language Magazine") or a section label
("Breaking News"), not the article's headline. Persisting one of those produces a
reel with no news in it (PRD RC4 — the "Language Magazine" reel).

This module is the single predicate three seams share (PRD decision 6):

  * ``agents/ingestion/dedup.py`` — pick the cluster representative by title quality
    (the earliest-published member is often the worst-titled one).
  * ``agents/pipeline/stages/editorial.py`` — refuse a rewrite that came back as a
    fragment, so the caller falls back rather than publishing it.
  * ``agents/pipeline/orchestrator.py`` / ``persist.py`` — the fail-closed gate: a
    story whose final title is unpublishable is dropped, never filed under junk.

Pure functions — no I/O, no logging (callers own the structured rejection log so the
story id travels with it).
"""

from __future__ import annotations

import re

# Reason: an informative headline is at minimum subject + verb + object ("Musk buys
# Twitter"), so 3 is the floor. It is deliberately NOT higher: real headlines sit at
# 3 words often enough ("Assad flees Syria") that a floor of 4 would drop genuine news,
# and this gate has no fallback — a rejected story is dropped, so a false positive
# costs a reel. The 1–2 word junk class ("Language Magazine", "Breaking News") is what
# the count is for; a longer masthead is caught by the outlet match instead.
MIN_HEADLINE_WORD_COUNT = 3

# Reason: outlet names differ from titles only by case/punctuation/leading article
# ("The Language Magazine" vs "Language Magazine"), so compare on a stripped form.
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_LEADING_ARTICLE = re.compile(r"^the ")


def _comparison_key(text: str) -> str:
    """Lowercase, article-stripped, alphanumeric-only form used for outlet matching.

    Args:
        text: Any title, outlet name, or outlet domain.

    Returns:
        The comparison key ("" when the text carries no alphanumerics).

    Example:
        >>> _comparison_key("The Language Magazine!") == _comparison_key("language magazine")
        True
    """
    lowered = _LEADING_ARTICLE.sub("", text.strip().lower())
    return _NON_ALPHANUMERIC.sub("", lowered)


def _domain_keys(outlet_domain: str) -> set[str]:
    """Every comparison key a domain could be written as in a title.

    Each non-TLD label plus their concatenation, because GDELT hands us hosts with
    subdomains ("edition.cnn.com", "timesofindia.indiatimes.com") and the masthead in
    the title is any one of those words — matching only the first label would miss
    "CNN" on ``edition.cnn.com``.

    Args:
        outlet_domain: An outlet host.

    Returns:
        The set of non-empty comparison keys ("edition.cnn.com" -> {edition, cnn,
        editioncnn}).

    Example:
        >>> "cnn" in _domain_keys("edition.cnn.com")
        True
    """
    host = outlet_domain.strip().lower().removeprefix("www.")
    labels = [label for label in host.split(".") if label][:-1]
    keys = {_comparison_key(label) for label in labels}
    keys.add(_comparison_key("".join(labels)))
    return {key for key in keys if key}


def headline_rejection_reason(
    title: str,
    outlet_name: str | None = None,
    outlet_domain: str | None = None,
) -> str | None:
    """Why this title cannot be published as a headline, or ``None`` if it can.

    There is deliberately no "probably fine" verdict: an unrecognized title is
    publishable and a matched one is rejected outright, because the fail-open
    fallback is what shipped the masthead reel (PRD decision 6).

    Args:
        title: The candidate headline (source title or editorial rewrite).
        outlet_name: The outlet's display name, when known.
        outlet_domain: The outlet's domain, when known.

    Returns:
        A short machine-readable reason (``"empty"``, ``"title_equals_outlet"``,
        ``"too_few_words"``), or ``None`` when the title is publishable.

    Example:
        >>> headline_rejection_reason("Language Magazine", "Language Magazine")
        'title_equals_outlet'
        >>> headline_rejection_reason("Fed holds rates steady", "Reuters") is None
        True
    """
    stripped = (title or "").strip()
    if not stripped:
        return "empty"

    title_key = _comparison_key(stripped)
    outlet_keys = {_comparison_key(outlet_name or "")}
    if outlet_domain:
        outlet_keys |= _domain_keys(outlet_domain)
    if title_key and title_key in outlet_keys:
        return "title_equals_outlet"

    if len(stripped.split()) < MIN_HEADLINE_WORD_COUNT:
        return "too_few_words"
    return None


def is_publishable_headline(
    title: str,
    outlet_name: str | None = None,
    outlet_domain: str | None = None,
) -> bool:
    """True when ``title`` is an informative headline rather than a masthead/fragment.

    Args:
        title: The candidate headline.
        outlet_name: The outlet's display name, when known.
        outlet_domain: The outlet's domain, when known.

    Returns:
        Whether the title may be published.

    Example:
        >>> is_publishable_headline("Language Magazine", "Language Magazine")
        False
    """
    return headline_rejection_reason(title, outlet_name, outlet_domain) is None
