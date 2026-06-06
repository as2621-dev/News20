"""X (Twitter) handle resolver — build-fresh, no donor analog (Phase 5c SP3a).

`reference/sources-reuse-map.md` §6 marks the X/Twitter handle resolver as
**NEW** (the donor's `twitter_account` source_type was added then pruned, and its
adapter never survived). This module resolves a free-text ``@handle`` (or an X /
twitter.com profile URL) into a normalized :class:`XAccountResolution` record the
worker source-search endpoint returns as a followable ``x_account`` source.

DESIGN (the phase DoD, Rule 12 — fail loud, never silently drop):
  • Parsing the handle is **pure + deterministic** — no network, always works:
    ``@Reuters`` / ``https://x.com/Reuters?lang=en`` / ``twitter.com/Reuters`` all
    normalize to the canonical handle ``Reuters`` and the external id ``Reuters``
    (lower-cased; X handles are case-insensitive). An unparseable input raises
    ``XHandleParseError`` rather than returning a bogus record.
  • LIVE resolution (display name + profile image via an X API) is an injectable
    seam (``live_lookup``) that is **OFF by default**: no X API key is wired yet
    (open question — which API, cost, rate limits). When no lookup is provided, or
    a provided lookup fails, the resolver returns a **pending** record
    (``is_pending=True``) carrying just the canonical handle — i.e. the handle is
    stored as a pending ``x_account`` free-text follow, exactly the DoD fallback.
    A pending record is a legitimate, addable result; it is never an error.

The live path is cleanly mockable: pass any async ``live_lookup`` callable
returning an :class:`XAccountLiveProfile` (or ``None``) — tests inject a stub, so
no test ever hits a real X API (CLAUDE.md mocking strategy).
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from agents.shared.exceptions import IngestionError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

_ADAPTER_NAME = "x_resolver"

# Reason: X handles are 1–15 chars, ASCII letters/digits/underscore only
# (the platform's documented rule). The regex is the single source of truth for
# what a valid handle is — both the bare-@handle and the URL paths funnel through it.
_HANDLE_BODY_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# Hosts we accept a profile URL from (x.com superseded twitter.com; both live).
_X_PROFILE_HOSTS = (
    "x.com",
    "twitter.com",
    "www.x.com",
    "www.twitter.com",
    "mobile.twitter.com",
)

# URL path segments that are X *features*, never a profile handle — reject these
# so e.g. ``x.com/home`` does not resolve to a phantom "@home" account.
_RESERVED_HANDLES = frozenset(
    {
        "home",
        "explore",
        "notifications",
        "messages",
        "i",
        "search",
        "settings",
        "compose",
        "intent",
    }
)


class XHandleParseError(IngestionError):
    """Raised when an input cannot be parsed into a valid X handle (fail loud).

    The free-text input was neither a recognizable ``@handle`` nor an X /
    twitter.com profile URL with a valid handle in its first path segment. The
    worker turns this into a clean "no results" response (the user typed garbage),
    never a crash.

    Attributes:
        raw_input: The offending raw input (truncated; never a secret).

    Example:
        >>> raise XHandleParseError(raw_input="https://x.com/home")
    """

    def __init__(
        self,
        raw_input: str,
        fix_suggestion: str = "Enter a handle like @Reuters or an x.com/twitter.com profile URL",
    ) -> None:
        self.raw_input = raw_input[:100]
        super().__init__(
            message=f"[{_ADAPTER_NAME}] could not parse an X handle from input: {self.raw_input!r}",
            fix_suggestion=fix_suggestion,
        )


class XAccountLiveProfile(BaseModel):
    """The enriched fields a live X-API lookup yields for one handle.

    Returned by an injected ``live_lookup`` callable. Both enrichment fields are
    optional so a partial lookup (handle confirmed, image missing) still upgrades
    the record out of pending.

    Attributes:
        display_name: The account's display name (e.g. "Reuters").
        profile_image_url: A URL to the account's avatar image.
    """

    display_name: str | None = Field(
        default=None,
        description="The X account's display name, if the lookup returned one.",
    )
    profile_image_url: str | None = Field(
        default=None,
        description="URL to the account's profile image, if the lookup returned one.",
    )


class XAccountResolution(BaseModel):
    """A normalized, addable ``x_account`` source resolved from a free-text handle.

    Mirrors the worker source-search result contract (name / external id / image /
    handle) so the worker can return it alongside YouTube/podcast results.

    Attributes:
        handle: The canonical handle WITHOUT the leading ``@`` (e.g. "Reuters").
        external_id: The platform id used for dedup/follow — the lower-cased
            handle (X handles are case-insensitive); stable across casings/URLs.
        display_name: The resolved display name, or the handle itself when the
            account could not be enriched (pending).
        profile_image_url: The resolved avatar URL, or ``None`` when pending.
        is_pending: ``True`` when the handle is stored as a pending free-text
            follow (no live enrichment) — the DoD fallback; ``False`` when a live
            lookup enriched it.
    """

    handle: str = Field(..., description="Canonical handle without the leading @.")
    external_id: str = Field(
        ..., description="Stable platform id for dedup/follow (lower-cased handle)."
    )
    display_name: str = Field(
        ..., description="Resolved display name, or the handle when pending."
    )
    profile_image_url: str | None = Field(
        default=None, description="Resolved avatar URL, or None when pending."
    )
    is_pending: bool = Field(
        ...,
        description="True when stored as a pending free-text follow (no live enrichment).",
    )


# An async callable that enriches a canonical handle into a live profile, or
# returns None when the account cannot be found / the API has no data. Injected so
# the live path is fully mockable and the default (no key wired) path is pending.
XLiveLookup = Callable[[str], Awaitable[XAccountLiveProfile | None]]


def parse_x_handle(raw_input: str) -> str:
    """Parse a free-text input into a canonical X handle (pure, no network).

    Accepts a bare ``@handle``, a bare ``handle``, or an x.com / twitter.com
    profile URL (with or without scheme, query string, or trailing slash). The
    handle is validated against X's character rules and returned WITHOUT the
    leading ``@``, preserving its original casing (display casing).

    Args:
        raw_input: The user's free-text source query (handle or profile URL).

    Returns:
        The canonical handle without the ``@`` (e.g. ``"Reuters"``).

    Raises:
        XHandleParseError: When no valid handle can be extracted.

    Example:
        >>> parse_x_handle("@Reuters")
        'Reuters'
        >>> parse_x_handle("https://x.com/Reuters?lang=en")
        'Reuters'
    """
    candidate = raw_input.strip()
    if not candidate:
        raise XHandleParseError(raw_input=raw_input)

    # URL form: pull the first non-empty path segment off a recognized X host.
    # Reason: handle both "https://x.com/Reuters" and bare "x.com/Reuters" (no
    # scheme) — strip an optional scheme, then split host/path.
    without_scheme = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", candidate)
    host_and_path = without_scheme.split("?", 1)[0].split("#", 1)[0]
    if "/" in host_and_path:
        host, _, path = host_and_path.partition("/")
        if host.lower() in _X_PROFILE_HOSTS:
            first_segment = next((seg for seg in path.split("/") if seg), "")
            return _validate_handle(first_segment, raw_input)
        # A slash but not an X host → not a profile URL we understand.
        raise XHandleParseError(raw_input=raw_input)

    # Bare-handle form: drop a single leading @, then validate.
    return _validate_handle(candidate.lstrip("@"), raw_input)


def _validate_handle(handle: str, raw_input: str) -> str:
    """Validate a stripped handle against X's rules + the reserved-word denylist.

    Args:
        handle: The candidate handle (no ``@``, no URL parts).
        raw_input: The original input, for the error's context.

    Returns:
        The validated handle unchanged.

    Raises:
        XHandleParseError: When the handle is empty, malformed, or reserved.
    """
    if not _HANDLE_BODY_PATTERN.match(handle) or handle.lower() in _RESERVED_HANDLES:
        raise XHandleParseError(raw_input=raw_input)
    return handle


async def resolve_x_handle(
    raw_input: str,
    live_lookup: XLiveLookup | None = None,
) -> XAccountResolution:
    """Resolve a free-text X input into a normalized, addable ``x_account`` record.

    Always parses the handle first (pure, deterministic). Then, if a
    ``live_lookup`` is provided, attempts enrichment (display name + avatar); on
    any lookup failure OR no lookup at all, returns a **pending** record carrying
    just the handle — the DoD fallback ("store the handle as a pending x_account
    free-text follow"). A pending record is a valid result, never an error.

    Args:
        raw_input: The user's free-text query (``@handle`` or profile URL).
        live_lookup: Optional async enrichment callable. When ``None`` (the
            default — no X API key wired), the result is always pending.

    Returns:
        A resolved :class:`XAccountResolution` (``is_pending=False``) when live
        enrichment succeeds, else a pending one (``is_pending=True``).

    Raises:
        XHandleParseError: When the input is not a parseable handle/URL.

    Example:
        >>> # pending fallback (no live lookup wired):
        >>> rec = await resolve_x_handle("@Reuters")
        >>> rec.is_pending, rec.handle, rec.external_id
        (True, 'Reuters', 'reuters')
    """
    handle = parse_x_handle(raw_input)
    external_id = handle.lower()

    if live_lookup is None:
        logger.info(
            "x_resolve_pending",
            adapter=_ADAPTER_NAME,
            handle=handle,
            reason="no_live_lookup_configured",
        )
        return _pending_resolution(handle, external_id)

    try:
        profile = await live_lookup(handle)
    except Exception as exc:  # noqa: BLE001 — boundary: a lookup failure degrades to pending, never crashes
        logger.error(
            "x_resolve_live_lookup_failed",
            adapter=_ADAPTER_NAME,
            handle=handle,
            error_message=str(exc)[:200],
            fix_suggestion="X live lookup failed; stored as a pending free-text follow. "
            "Verify the X API choice/key/quota (open question #3).",
        )
        return _pending_resolution(handle, external_id)

    if profile is None:
        logger.info(
            "x_resolve_pending",
            adapter=_ADAPTER_NAME,
            handle=handle,
            reason="live_lookup_returned_no_profile",
        )
        return _pending_resolution(handle, external_id)

    logger.info("x_resolve_enriched", adapter=_ADAPTER_NAME, handle=handle)
    return XAccountResolution(
        handle=handle,
        external_id=external_id,
        display_name=profile.display_name or handle,
        profile_image_url=profile.profile_image_url,
        is_pending=False,
    )


def _pending_resolution(handle: str, external_id: str) -> XAccountResolution:
    """Build a pending ``x_account`` record (handle only, no enrichment).

    Args:
        handle: The canonical handle (display casing).
        external_id: The lower-cased handle (dedup/follow id).

    Returns:
        A pending :class:`XAccountResolution` whose display name IS the handle.
    """
    return XAccountResolution(
        handle=handle,
        external_id=external_id,
        display_name=handle,
        profile_image_url=None,
        is_pending=True,
    )
