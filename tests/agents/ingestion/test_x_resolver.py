"""Unit tests for the build-fresh X-handle resolver (Phase 5c SP3a).

WHY (Rule 9 — encode the contract, not the call shape):
  • Parsing must normalize EVERY accepted form (@handle, bare handle, x.com /
    twitter.com URL with scheme/query/trailing-slash) to the SAME canonical
    handle + lower-cased external id — otherwise the same account followed via two
    inputs would create two follow rows (a dedup bug). So we assert the
    normalization across forms, not just one.
  • The DoD fallback: with NO live X lookup wired (the current reality — open
    question #3), an @handle MUST resolve to a PENDING free-text follow
    (is_pending=True), never throw and never silently drop. That is the whole
    point of the build-fresh resolver, so it has the most coverage.
  • A live lookup, when injected (mocked), MUST upgrade the record out of pending
    AND a live-lookup FAILURE must degrade BACK to pending (never crash) — the two
    halves of the mockable seam.
  • Garbage input (a feature URL, an empty string, an over-long handle) MUST raise
    XHandleParseError (fail loud) — never a phantom account.

No network: the live path is an injected async stub (CLAUDE.md mocking strategy).

    >>> pytest tests/agents/ingestion/test_x_resolver.py -v
"""

from __future__ import annotations

import pytest

from agents.ingestion.adapters.x_resolver import (
    XAccountLiveProfile,
    XHandleParseError,
    parse_x_handle,
    resolve_x_handle,
)


class TestParseXHandle:
    """parse_x_handle normalizes every accepted form to the canonical handle."""

    @pytest.mark.parametrize(
        "raw_input,expected",
        [
            ("@Reuters", "Reuters"),
            ("Reuters", "Reuters"),
            ("  @Reuters  ", "Reuters"),
            ("https://x.com/Reuters", "Reuters"),
            ("https://x.com/Reuters?lang=en", "Reuters"),
            ("https://twitter.com/Reuters/", "Reuters"),
            ("x.com/Reuters", "Reuters"),  # no scheme
            ("https://www.twitter.com/Reuters#section", "Reuters"),
            ("https://mobile.twitter.com/Reuters", "Reuters"),
        ],
    )
    def test_normalizes_accepted_forms_to_canonical_handle(
        self, raw_input: str, expected: str
    ) -> None:
        """All accepted forms collapse to the same canonical handle (dedup safety)."""
        assert parse_x_handle(raw_input) == expected

    def test_preserves_casing_in_handle_but_lowercases_external_id(self) -> None:
        """Handle keeps display casing; external_id is lower-cased (case-insensitive id)."""
        # parse keeps casing; the external id is lower-cased in resolve (asserted there).
        assert parse_x_handle("@ReUtErS") == "ReUtErS"

    @pytest.mark.parametrize(
        "raw_input",
        [
            "",  # empty
            "   ",  # whitespace only
            "@",  # @ with no body
            "@this_handle_is_way_too_long",  # > 15 chars
            "@bad-handle",  # hyphen is illegal in X handles
            "https://x.com/home",  # reserved feature path, not a profile
            "https://x.com/",  # host but no handle segment
            "https://example.com/Reuters",  # not an X host
            "example.com/Reuters",  # not an X host (no scheme)
        ],
    )
    def test_raises_on_unparseable_input(self, raw_input: str) -> None:
        """Garbage / feature URLs / non-X hosts fail loud, never a phantom handle."""
        with pytest.raises(XHandleParseError):
            parse_x_handle(raw_input)


class TestResolveXHandlePendingFallback:
    """The DoD fallback: no live lookup → a pending free-text follow."""

    @pytest.mark.asyncio
    async def test_pending_when_no_live_lookup_configured(self) -> None:
        """With live_lookup=None (no X API wired), the @handle is stored as PENDING."""
        resolution = await resolve_x_handle("@Reuters")

        assert resolution.is_pending is True
        assert resolution.handle == "Reuters"
        assert resolution.external_id == "reuters"  # lower-cased id
        assert resolution.display_name == "Reuters"  # falls back to the handle
        assert resolution.profile_image_url is None

    @pytest.mark.asyncio
    async def test_pending_external_id_is_stable_across_casing_and_url(self) -> None:
        """A URL and a differently-cased @handle for one account share an external_id."""
        from_url = await resolve_x_handle("https://x.com/ReUtErS")
        from_handle = await resolve_x_handle("@reuters")

        assert from_url.external_id == from_handle.external_id == "reuters"


class TestResolveXHandleLivePath:
    """The injectable live seam — mocked, never a real X API (CLAUDE.md)."""

    @pytest.mark.asyncio
    async def test_live_lookup_enriches_out_of_pending(self) -> None:
        """A successful mocked lookup upgrades the record (name + image, not pending)."""

        async def fake_lookup(handle: str) -> XAccountLiveProfile:
            assert handle == "Reuters"
            return XAccountLiveProfile(
                display_name="Reuters",
                profile_image_url="https://pbs.example/reuters.jpg",
            )

        resolution = await resolve_x_handle("@Reuters", live_lookup=fake_lookup)

        assert resolution.is_pending is False
        assert resolution.display_name == "Reuters"
        assert resolution.profile_image_url == "https://pbs.example/reuters.jpg"
        assert resolution.external_id == "reuters"

    @pytest.mark.asyncio
    async def test_live_lookup_returning_none_falls_back_to_pending(self) -> None:
        """A lookup that finds no profile (None) degrades to pending, not an error."""

        async def fake_lookup(_handle: str) -> None:
            return None

        resolution = await resolve_x_handle("@Reuters", live_lookup=fake_lookup)
        assert resolution.is_pending is True
        assert resolution.profile_image_url is None

    @pytest.mark.asyncio
    async def test_live_lookup_failure_degrades_to_pending_never_crashes(self) -> None:
        """A raising lookup is caught → pending fallback (the resolver never crashes)."""

        async def boom(_handle: str) -> XAccountLiveProfile:
            raise RuntimeError("X API 429")

        resolution = await resolve_x_handle("@Reuters", live_lookup=boom)
        assert resolution.is_pending is True
        assert resolution.handle == "Reuters"

    @pytest.mark.asyncio
    async def test_unparseable_input_still_raises_with_live_lookup(self) -> None:
        """Parse happens BEFORE the live lookup — garbage raises regardless of the seam."""

        async def never_called(_handle: str) -> XAccountLiveProfile:
            raise AssertionError("live_lookup must not run for unparseable input")

        with pytest.raises(XHandleParseError):
            await resolve_x_handle("https://x.com/home", live_lookup=never_called)
