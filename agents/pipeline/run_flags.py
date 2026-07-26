"""Spend-safety run flags shared by every production entry point (issues #66, #65).

Two flags decide whether a daily run spends money and whether it admits stories on
one key or two. They were read INLINE in ``scripts/run_live_batch.py`` only, so the
deployed worker (``agents/worker/pipeline_routes.py``) silently ran with the LIBRARY
defaults instead — producing reels on every cron fire (#66) and admitting stories on
the lexical key alone (#65). Reading them here, once, is what makes the two entry
points structurally unable to drift again: change the default in this module and both
paths change together.

Both are OPT-OUT flags whose unset value is the SAFE one:

* ``SHORTLIST_ONLY`` (default ON) — halt at story selection, dump the would-produce
  list for founder review, spend zero script/TTS/poster credits (founder rule
  2026-07-19). Producing requires the explicit ``SHORTLIST_ONLY=0``.
* ``ENABLE_SEMANTIC_RELEVANCE_KEY`` (default ON) — run the semantic half of the
  two-key relevance lock (issue #51). ``=0`` falls back to lexical-only admission.

Only an explicit falsy spelling turns a safety default off. A typo'd value keeps the
safe behavior rather than silently authorizing spend — the failure mode of
``os.environ.get(name, "1") == "1"`` was the exact opposite: ``SHORTLIST_ONLY=true``
read as "produce".
"""

from __future__ import annotations

import os

SHORTLIST_ONLY_ENV_VAR: str = "SHORTLIST_ONLY"
SEMANTIC_RELEVANCE_KEY_ENV_VAR: str = "ENABLE_SEMANTIC_RELEVANCE_KEY"

# Reason: the ONLY spellings that may switch a safety default off. Anything else —
# unset, empty, "yes", a typo — leaves the safe behavior in place.
_FALSY_VALUES = frozenset({"0", "false", "no", "off"})


def _opt_out_flag_enabled(env_var_name: str) -> bool:
    """Return True unless ``env_var_name`` holds an explicit falsy spelling.

    Args:
        env_var_name: Name of the environment variable to read.

    Returns:
        False only for ``0``/``false``/``no``/``off`` (case- and
        whitespace-insensitive); True for everything else, including unset.
    """
    return os.environ.get(env_var_name, "").strip().lower() not in _FALSY_VALUES


def shortlist_only_enabled() -> bool:
    """Return True when the run must halt at the shortlist instead of producing.

    Founder rule 2026-07-19 (credit frugality, shortlist-first): reel production —
    script LLM, then TTS, then poster — is the expensive tail of a batch, so every
    entry point halts at story selection by default and production is opt-in.

    Returns:
        True when the run must stop after story selection (the default).

    Example:
        >>> os.environ["SHORTLIST_ONLY"] = "0"  # doctest: +SKIP
        >>> shortlist_only_enabled()  # doctest: +SKIP
        False
    """
    return _opt_out_flag_enabled(SHORTLIST_ONLY_ENV_VAR)


def semantic_relevance_key_enabled() -> bool:
    """Return True when ingestion must run the SEMANTIC half of the relevance lock.

    Issue #51: a story keeps a matched interest only when its embedding similarity
    also clears the threshold, which closes the RC3 false positives (the
    zoning/"data center" class) that the lexical key alone admits. Costs one batched
    ``gemini-embedding-001`` call per run. An embedding outage does not fail the run
    — ingestion falls back to strict lexical and stamps the run ``degraded``.

    Returns:
        True when the semantic key must be armed (the default).

    Example:
        >>> os.environ["ENABLE_SEMANTIC_RELEVANCE_KEY"] = "0"  # doctest: +SKIP
        >>> semantic_relevance_key_enabled()  # doctest: +SKIP
        False
    """
    return _opt_out_flag_enabled(SEMANTIC_RELEVANCE_KEY_ENV_VAR)
