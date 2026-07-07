"""Poster kill switch (issue #32): ``DISABLE_POSTER_GEN`` stops all image-model spend.

One env var makes poster image generation impossible on every production entry
point (worker route, ``scripts/run_live_batch.py``, ``scripts/produce_source_reels.py``,
and the ``scripts/fill_batch_posters.py`` second pass). Entry points call
:func:`poster_generation_disabled` to skip constructing a ``google.genai.Client``
at all; the orchestrator choke point (``generate_poster_bytes``) calls it again as
defense-in-depth, forcing the poster client to ``None`` — which the pipeline
already degrades on gracefully (posterless story persists; the reel renders the
category wash). The FREE supplied-image path for source-origin reels (YouTube
thumbnail / X screenshot download+grade) never touches the client, so it keeps
working with the switch on.

Flag unset (or ``0``/``false``) → behavior byte-identical to today, so
re-enabling posters is env-only.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger()

# Reason: the disabled warning must fire once per run, not once per story — a
# 300-story batch would otherwise drown the logs in identical lines.
_poster_disabled_logged = False

_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def poster_generation_disabled() -> bool:
    """Return True when the ``DISABLE_POSTER_GEN`` kill switch is on.

    Accepts the usual truthy spellings (``1``/``true``/``TRUE``/``yes``/``on``,
    whitespace-tolerant); anything else — including unset — keeps posters on.
    Logs a single structured warning per process when disabled.

    Returns:
        True when all image-model poster spend must be skipped.

    Example:
        >>> os.environ["DISABLE_POSTER_GEN"] = "1"  # doctest: +SKIP
        >>> poster_generation_disabled()  # doctest: +SKIP
        True
    """
    global _poster_disabled_logged
    disabled = (
        os.environ.get("DISABLE_POSTER_GEN", "").strip().lower() in _TRUTHY_VALUES
    )
    if disabled and not _poster_disabled_logged:
        logger.warning(
            "poster_generation_disabled",
            fix_suggestion=(
                "unset DISABLE_POSTER_GEN or set it to 0 to re-enable poster generation"
            ),
        )
        _poster_disabled_logged = True
    return disabled
