"""Tests for the followed-source CadenceScheduler (Phase 5d SP3).

WHY these matter (Rule 9): the cadence gate is the only thing stopping the 2h cron
from re-polling every followed source on every tick (cost + rate-limit blast). The
tests encode the business rule — a source polled within its 6h window is NOT due —
not just that the arithmetic runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.ingestion.scheduler import CadenceScheduler

_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


class TestIsSourceDue:
    """is_source_due — the poll-or-skip decision."""

    def test_never_polled_source_is_always_due(self) -> None:
        """A source with last_fetched_at None has never run → must be polled."""
        scheduler = CadenceScheduler()
        assert scheduler.is_source_due("youtube_channel", None, _NOW) is True

    def test_within_window_is_not_due(self) -> None:
        """Polled 2h ago, 6h cadence → NOT due (the cron-tick re-poll guard)."""
        scheduler = CadenceScheduler()
        last = _NOW - timedelta(hours=2)
        assert scheduler.is_source_due("youtube_channel", last, _NOW) is False

    def test_past_window_is_due(self) -> None:
        """Polled 7h ago, 6h cadence → due again."""
        scheduler = CadenceScheduler()
        last = _NOW - timedelta(hours=7)
        assert scheduler.is_source_due("x_account", last, _NOW) is True

    def test_exactly_at_window_boundary_is_due(self) -> None:
        """At exactly the cadence boundary the source is due (>= comparison)."""
        scheduler = CadenceScheduler()
        last = _NOW - timedelta(hours=6)
        assert scheduler.is_source_due("youtube_channel", last, _NOW) is True

    def test_naive_last_fetched_assumed_utc(self) -> None:
        """A naive last_fetched_at is treated as UTC (no crash, correct window)."""
        scheduler = CadenceScheduler()
        naive_last = datetime(2026, 6, 17, 10, 0, 0)  # 2h before _NOW, no tzinfo
        assert scheduler.is_source_due("youtube_channel", naive_last, _NOW) is False

    def test_unknown_type_uses_fallback_cadence(self) -> None:
        """An out-of-scope type (podcast) uses the fallback cadence, not 0."""
        scheduler = CadenceScheduler()
        last = _NOW - timedelta(hours=2)
        # Fallback is 6h, so 2h ago is still not due.
        assert scheduler.is_source_due("podcast", last, _NOW) is False

    def test_configurable_cadence_override(self) -> None:
        """An injected cadence map overrides the default window."""
        scheduler = CadenceScheduler(cadence_hours={"youtube_channel": 1.0})
        last = _NOW - timedelta(hours=2)
        # 1h cadence, polled 2h ago → due.
        assert scheduler.is_source_due("youtube_channel", last, _NOW) is True


class TestFetchSince:
    """fetch_since — the cutoff handed to the adapter."""

    def test_previously_polled_uses_last_fetched(self) -> None:
        """A polled source fetches only items newer than its last poll."""
        scheduler = CadenceScheduler()
        last = _NOW - timedelta(hours=8)
        assert scheduler.fetch_since("youtube_channel", last, _NOW) == last

    def test_never_polled_uses_bounded_cold_start(self) -> None:
        """A fresh follow uses a bounded lookback, not the epoch (no back-catalogue)."""
        scheduler = CadenceScheduler()
        since = scheduler.fetch_since(
            "youtube_channel", None, _NOW, cold_start_lookback_hours=24.0
        )
        assert since == _NOW - timedelta(hours=24)

    def test_cadence_for_known_and_unknown(self) -> None:
        """cadence_for returns the mapped window, falling back for unknown types."""
        scheduler = CadenceScheduler(fallback_cadence_hours=9.0)
        assert scheduler.cadence_for("youtube_channel") == 6.0
        assert scheduler.cadence_for("personality") == 9.0
