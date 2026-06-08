"""Offline tests for the X avatar resolver (Phase 5f SP3).

Rule 9 — tests encode WHY:
  - Taxonomy Q2/Q3 locks the X avatar as a HOT-LINK to ``unavatar.io/x/<handle>``
    (unavatar serves the real avatar when it can, a generated one otherwise), so
    a ``200 image/*`` probe confirms a real cached avatar and the resolver returns
    the hot-link URL. WHY: the URL must render in the app; the probe only signals
    realness for logging.
  - A ``404`` is a confirmed no-such-avatar, so the resolver returns None. WHY:
    storing a null thumbnail is honest — the app falls back to its own initials
    avatar and the row does not claim a real photo.
  - A ``429`` is unavatar's per-IP daily anonymous rate limit (25/day free), which
    means "could not verify", NOT "no avatar" — so the resolver KEEPS the hot-link.
    WHY: dropping the avatar on a rate-limit would silently tank coverage for a
    reason unrelated to the handle's realness (the cap resets daily / lifts with a
    key); keeping the hot-link is both honest and renders on a fresh day.
  - A transport error keeps the hot-link and never raises. WHY: one unreachable
    probe must not abort a batch (Rule 12 — fail per row, not the whole run).

The unavatar HTTP call is mocked at the httpx boundary (CLAUDE.md) — no network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from scripts.seed_catalog import x_resolve


class _FakeResponse:
    """A minimal stand-in for an ``httpx.Response`` (status + headers)."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


def _image(status_code: int = 200) -> _FakeResponse:
    return _FakeResponse(status_code, {"content-type": "image/jpeg"})


class _FixedClient:
    """Returns a fixed response for every probe, recording the URLs requested."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[str] = []

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float = 0.0,
        follow_redirects: bool = False,
    ) -> _FakeResponse:
        self.calls.append(url)
        return self._response


class _RaisingClient:
    """Raises an ``httpx`` transport error on every probe (network failure)."""

    async def get(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        raise httpx.ConnectError("boom")


# ── Happy path: a 200 image confirms + returns the hot-link URL ────────────────


def test_resolve_avatar_200_image_returns_hotlink_url() -> None:
    """WHY: a verified real avatar (200 image/*) must store the renderable hot-link."""
    client = _FixedClient(_image(200))
    url = asyncio.run(x_resolve.resolve_avatar(handle="@karpathy", client=client))  # type: ignore[arg-type]
    assert url == "https://unavatar.io/x/karpathy"
    # The probe hits the plain hot-link URL (no fallback flag).
    assert client.calls == ["https://unavatar.io/x/karpathy"]


# ── Confirmed miss: a 404 yields None (null thumbnail, honest) ─────────────────


def test_resolve_avatar_404_is_confirmed_miss_returns_none() -> None:
    """WHY: a confirmed no-avatar (404) → null thumbnail, not a dead hot-link."""
    client = _FixedClient(_FakeResponse(404))
    url = asyncio.run(x_resolve.resolve_avatar(handle="ghosthandle", client=client))  # type: ignore[arg-type]
    assert url is None


# ── Rate limit: a 429 keeps the hot-link (could-not-verify, not no-avatar) ─────


def test_resolve_avatar_429_keeps_hotlink_not_dropped() -> None:
    """WHY: a per-IP daily cap means 'unverified', not 'no avatar' — keep the link."""
    client = _FixedClient(_FakeResponse(429, {"retry-after": "31000"}))
    url = asyncio.run(x_resolve.resolve_avatar(handle="@sama", client=client))  # type: ignore[arg-type]
    assert url == "https://unavatar.io/x/sama"


# ── Failure case: a transport error keeps the hot-link, never raises ───────────


def test_resolve_avatar_network_error_keeps_hotlink_not_raises() -> None:
    """WHY: one unreachable probe must not abort the batch (Rule 12 — fail per row)."""
    url = asyncio.run(
        x_resolve.resolve_avatar(handle="anyone", client=_RaisingClient())  # type: ignore[arg-type]
    )
    assert url == "https://unavatar.io/x/anyone"


# ── Edge case: blank handle short-circuits without a probe ─────────────────────


def test_resolve_avatar_blank_handle_returns_none_without_probe() -> None:
    """WHY: a blank handle is not a real account — never waste a probe on it."""
    client = _FixedClient(_image(200))
    url = asyncio.run(x_resolve.resolve_avatar(handle="  @  ", client=client))  # type: ignore[arg-type]
    assert url is None
    assert client.calls == []


# ── Batch: resolve_many keys by lowercased bare handle, drops confirmed-404 ────


def test_resolve_many_keys_by_bare_handle_drops_confirmed_404() -> None:
    """WHY: the seeder looks avatars up by lowercased bare handle; 404s omitted."""

    class _MixedClient:
        async def get(
            self,
            url: str,
            params: dict[str, Any] | None = None,
            timeout: float = 0.0,
            follow_redirects: bool = False,
        ) -> _FakeResponse:
            # "missing" is a confirmed 404; everything else is a real 200 image.
            if url.endswith("/missing"):
                return _FakeResponse(404)
            return _image(200)

    avatars = asyncio.run(
        x_resolve.resolve_many(
            ["@Karpathy", "@SAMA", "@missing"], client=_MixedClient()  # type: ignore[arg-type]
        )
    )
    assert avatars == {
        "karpathy": "https://unavatar.io/x/karpathy",
        "sama": "https://unavatar.io/x/sama",
    }
    assert "missing" not in avatars
