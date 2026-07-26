"""Spend-safety run flags shared by every production entry point (issues #66, #65, #68).

The flags below decide HOW FAR a daily run is allowed to go (and whether it admits
stories on one key or two). They were read INLINE in ``scripts/run_live_batch.py`` only,
so the deployed worker (``agents/worker/pipeline_routes.py``) silently ran with the LIBRARY
defaults instead — producing reels on every cron fire (#66) and admitting stories on
the lexical key alone (#65). Reading them here, once, is what makes the two entry
points structurally unable to drift again: change the default in this module and both
paths change together.

THE THREE-STAGE PRODUCTION LADDER (issue #68, founder decision 2026-07-25)
--------------------------------------------------------------------------
A run halts at one of three rungs, resolved ONCE by :func:`resolve_run_stage`::

    shortlist  →  founder review  →  scripts (+ similarity gate)  →  founder go  →  reels
    ^ default                        ^ SHORTLIST_ONLY=0                            ^ + PRODUCE_REELS=1

* ``RUN_STAGE_SHORTLIST`` — halt at story selection. Zero LLM production spend.
* ``RUN_STAGE_SCRIPTS`` — write the scripts, run the similarity gate over them,
  dump them for review, halt BEFORE any TTS/poster/audio call. Scripts are
  ``gemini-3.5-flash`` pennies; reels are the expensive tail (the 2026-07-19 $24
  burn was 58 reels' TTS + posters).
* ``RUN_STAGE_REELS`` — the full paid produce + feed assembly.

Climbing a rung is always an EXPLICIT act, and each rung's own guard is independent:
``SHORTLIST_ONLY=0`` buys scripts and nothing more, because the reel stage is armed
by its own opt-in ``PRODUCE_REELS=1``. A stale arm left in a Railway dashboard
therefore cannot resurrect spend once the shortlist halt is back on.

Opt-OUT flags (unset = ON = the safe value; only an explicit falsy spelling disables):

* ``SHORTLIST_ONLY`` (default ON) — halt at story selection, dump the would-produce
  list for founder review, spend zero script/TTS/poster credits (founder rule
  2026-07-19). Reaching the script stage requires the explicit ``SHORTLIST_ONLY=0``.
* ``ENABLE_SEMANTIC_RELEVANCE_KEY`` (default ON) — run the semantic half of the
  two-key relevance lock (issue #51). ``=0`` falls back to lexical-only admission.

Opt-IN flags (unset = OFF = the safe value; only an explicit truthy spelling enables):

* ``PRODUCE_REELS`` (default OFF) — arm the paid reel stage (TTS → poster → persist
  → daily_feeds) for THIS run. Without it every run stops at the scripts.
* ``SCRIPTS_ONLY`` (default OFF) — request the script halt explicitly. Redundant with
  the default (an unarmed run already stops there) but the spelling an operator
  reaches for; it also OUTRANKS ``PRODUCE_REELS`` so the two together cannot spend.

Only an explicit falsy spelling turns a safety default off, and only an explicit
truthy spelling arms spend. A typo'd value keeps the safe behavior rather than
silently authorizing spend — the failure mode of ``os.environ.get(name, "1") == "1"``
was the exact opposite: ``SHORTLIST_ONLY=true`` read as "produce".
"""

from __future__ import annotations

import os

SHORTLIST_ONLY_ENV_VAR: str = "SHORTLIST_ONLY"
SEMANTIC_RELEVANCE_KEY_ENV_VAR: str = "ENABLE_SEMANTIC_RELEVANCE_KEY"
SCRIPTS_ONLY_ENV_VAR: str = "SCRIPTS_ONLY"
PRODUCE_REELS_ENV_VAR: str = "PRODUCE_REELS"

# The three rungs of the production ladder (issue #68). One of these is what
# ``resolve_run_stage`` returns; entry points branch on it, never on raw env reads.
RUN_STAGE_SHORTLIST: str = "shortlist"
RUN_STAGE_SCRIPTS: str = "scripts"
RUN_STAGE_REELS: str = "reels"

# Reason: the ONLY spellings that may switch a safety default off. Anything else —
# unset, empty, "yes", a typo — leaves the safe behavior in place.
_FALSY_VALUES = frozenset({"0", "false", "no", "off"})

# Reason: the mirror set for OPT-IN flags — the only spellings that may ARM spend.
# Anything else, including a typo, leaves the run unarmed.
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


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


def _opt_in_flag_enabled(env_var_name: str) -> bool:
    """Return True only when ``env_var_name`` holds an explicit truthy spelling.

    The mirror of :func:`_opt_out_flag_enabled` for flags whose safe value is OFF:
    an unset, empty, or unrecognized value must never arm spend.

    Args:
        env_var_name: Name of the environment variable to read.

    Returns:
        True only for ``1``/``true``/``yes``/``on`` (case- and
        whitespace-insensitive); False for everything else, including unset.
    """
    return os.environ.get(env_var_name, "").strip().lower() in _TRUTHY_VALUES


def scripts_only_requested() -> bool:
    """Return True when the operator explicitly asked to halt at the scripts.

    Redundant with the unarmed default (an un-armed run already stops at the
    scripts) but it is the spelling an operator reaches for, and it OUTRANKS
    :func:`reels_armed` so ``SCRIPTS_ONLY=1 PRODUCE_REELS=1`` cannot spend.

    Returns:
        True when ``SCRIPTS_ONLY`` holds a truthy spelling.
    """
    return _opt_in_flag_enabled(SCRIPTS_ONLY_ENV_VAR)


def reels_armed() -> bool:
    """Return True when the paid reel stage is armed for THIS run (issue #68).

    Founder decision 2026-07-25 (staged production): TTS + posters are the
    expensive tail, so they run only on an explicit per-run arm. Approving the
    shortlist (``SHORTLIST_ONLY=0``) buys the scripts, never the reels.

    Returns:
        True only when ``PRODUCE_REELS`` holds a truthy spelling (default False).

    Example:
        >>> os.environ["PRODUCE_REELS"] = "1"  # doctest: +SKIP
        >>> reels_armed()  # doctest: +SKIP
        True
    """
    return _opt_in_flag_enabled(PRODUCE_REELS_ENV_VAR)


def resolve_run_stage() -> str:
    """Resolve how far this run may go — the ONE place the ladder's order lives.

    Precedence, safest first (issue #68): ``SHORTLIST_ONLY`` outranks everything,
    then an explicit ``SCRIPTS_ONLY``, and only an un-halted run that is also armed
    reaches the reels. Resolving it here (rather than each entry point combining the
    flags itself) is what keeps the local script, the worker, and any future caller
    from disagreeing about what "approved" means.

    Returns:
        One of :data:`RUN_STAGE_SHORTLIST`, :data:`RUN_STAGE_SCRIPTS`,
        :data:`RUN_STAGE_REELS`.

    Example:
        >>> resolve_run_stage()  # nothing set  # doctest: +SKIP
        'shortlist'
    """
    if shortlist_only_enabled():
        return RUN_STAGE_SHORTLIST
    if scripts_only_requested() or not reels_armed():
        return RUN_STAGE_SCRIPTS
    return RUN_STAGE_REELS
