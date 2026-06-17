"""Cadence scheduler for followed-source polling (Phase 5d SP3).

Ported (PATTERN) from the TL;DW donor ``agents/ingestion/scheduler.py``
``CadenceScheduler`` (reference/sources-reuse-map.md §3). The donor keyed cadence
by source type (YouTube 6h / podcast 12h / personality 6h); News20 keeps the
shape but scopes to the two axes this phase ships (YouTube + X, both 6h, both
configurable) — podcast is explicitly out of scope (plans/phase-5d-source-ingestion.md).

The scheduler is **pure over its inputs**: it decides "is this source due to be
re-fetched?" from the source's ``content_sources.content_source_type`` and its
``content_sources.last_fetched_at`` against an injected ``now``. No DB, no clock
dependency — fully unit-testable. The source_pipeline (this sub-phase) consults it
before dispatching an adapter, and stamps ``last_fetched_at`` after a successful
poll (the DB write is the caller's job; the scheduler only reads the value).

Example:
    >>> from datetime import datetime, timedelta, timezone
    >>> scheduler = CadenceScheduler()
    >>> now = datetime(2026, 6, 17, 12, tzinfo=timezone.utc)
    >>> scheduler.is_source_due("youtube_channel", now - timedelta(hours=7), now)
    True
    >>> scheduler.is_source_due("youtube_channel", now - timedelta(hours=2), now)
    False
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.shared.logger import get_logger

logger = get_logger("ingestion.scheduler")

# Reason: per-source-type poll cadence in hours (reference/sources-reuse-map.md §3
# — donor CadenceScheduler). YouTube + X both poll every 6h this phase; the cron
# (SP4) fires every 2h, so this gate enforces the per-source minimum interval so a
# source is not re-fetched on every cron tick. The enum values are
# content_sources.content_source_type (migration 0009): 'youtube_channel' /
# 'x_account' (NOT 'youtube' / 'x' — the schema uses the verbose forms).
_DEFAULT_CADENCE_HOURS: dict[str, float] = {
    "youtube_channel": 6.0,
    "x_account": 6.0,
}

# Reason: an unknown / out-of-scope source type (e.g. 'podcast', 'personality')
# falls back to this cadence rather than being polled every tick — conservative,
# and logged so a missing entry surfaces instead of silently mis-scheduling.
_FALLBACK_CADENCE_HOURS = 6.0


class CadenceScheduler:
    """Decides whether a followed source is due to be re-polled.

    A source is "due" when it has never been fetched (``last_fetched_at is None``)
    or when at least its per-type cadence has elapsed since the last fetch. The
    cadence map is injectable so tests (and a later control-surface tune) can
    override the per-type windows without touching this class.

    Attributes:
        cadence_hours: Map ``content_source_type -> minimum hours between polls``.
        fallback_cadence_hours: Cadence used for a source type absent from the map.

    Example:
        >>> scheduler = CadenceScheduler(cadence_hours={"youtube_channel": 6.0})
        >>> scheduler.cadence_for("youtube_channel")
        6.0
    """

    def __init__(
        self,
        cadence_hours: dict[str, float] | None = None,
        fallback_cadence_hours: float = _FALLBACK_CADENCE_HOURS,
    ) -> None:
        """Build the scheduler.

        Args:
            cadence_hours: Optional override of the per-type cadence map. When
                None, the default (YouTube 6h / X 6h) is used.
            fallback_cadence_hours: Cadence for a source type not in the map.
        """
        self.cadence_hours = dict(cadence_hours or _DEFAULT_CADENCE_HOURS)
        self.fallback_cadence_hours = fallback_cadence_hours

    def cadence_for(self, content_source_type: str) -> float:
        """Return the poll cadence (hours) for a source type.

        Args:
            content_source_type: The ``content_sources.content_source_type`` value.

        Returns:
            The minimum hours between polls for that type (the fallback when the
            type is unknown — logged once at WARNING so the gap is visible).
        """
        cadence = self.cadence_hours.get(content_source_type)
        if cadence is None:
            logger.warning(
                "scheduler_unknown_source_type",
                content_source_type=content_source_type,
                fallback_cadence_hours=self.fallback_cadence_hours,
                fix_suggestion="Add this content_source_type to the CadenceScheduler "
                "cadence map; using the fallback cadence for now",
            )
            return self.fallback_cadence_hours
        return cadence

    def is_source_due(
        self,
        content_source_type: str,
        last_fetched_at: datetime | None,
        now_utc: datetime,
    ) -> bool:
        """Decide whether a source should be re-fetched at ``now_utc``.

        A source is due when it has never been polled (``last_fetched_at`` is None)
        or when its per-type cadence has fully elapsed since the last poll.

        Args:
            content_source_type: The ``content_sources.content_source_type`` value.
            last_fetched_at: ``content_sources.last_fetched_at`` (tz-aware UTC; a
                naive value is assumed UTC). None means never polled → always due.
            now_utc: The current time (tz-aware UTC; injected for testability).

        Returns:
            True when the source is due to be polled, else False.

        Example:
            >>> from datetime import datetime, timedelta, timezone
            >>> now = datetime(2026, 6, 17, 12, tzinfo=timezone.utc)
            >>> CadenceScheduler().is_source_due("x_account", None, now)
            True
        """
        if last_fetched_at is None:
            return True

        last = (
            last_fetched_at
            if last_fetched_at.tzinfo
            else last_fetched_at.replace(tzinfo=timezone.utc)
        )
        cadence = self.cadence_for(content_source_type)
        next_due_at = last + timedelta(hours=cadence)
        return now_utc >= next_due_at

    def fetch_since(
        self,
        content_source_type: str,
        last_fetched_at: datetime | None,
        now_utc: datetime,
        *,
        cold_start_lookback_hours: float = 24.0,
    ) -> datetime:
        """Compute the ``since`` cutoff an adapter should fetch new items after.

        For a previously-polled source the cutoff is its ``last_fetched_at`` (only
        content newer than the last poll is new). For a never-polled source the
        cutoff is a bounded cold-start lookback before ``now`` (so a brand-new
        follow surfaces recent content without pulling the whole back-catalogue).

        Args:
            content_source_type: The source type (unused today; kept for a future
                per-type cold-start window without changing the signature).
            last_fetched_at: ``content_sources.last_fetched_at`` (None → cold start).
            now_utc: The current time (tz-aware UTC).
            cold_start_lookback_hours: Lookback window for a never-polled source.

        Returns:
            The tz-aware UTC cutoff to pass to the adapter's ``fetch_new_items``.
        """
        if last_fetched_at is None:
            return now_utc - timedelta(hours=cold_start_lookback_hours)
        return (
            last_fetched_at
            if last_fetched_at.tzinfo
            else last_fetched_at.replace(tzinfo=timezone.utc)
        )
