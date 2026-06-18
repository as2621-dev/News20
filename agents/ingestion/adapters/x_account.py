"""X (Twitter) source adapter — xAI/Grok Agent Tools (x_search) discovery + screenshot.

Like the YouTube adapter (``youtube.py``), X ingestion is **source-keyed**: it
polls one followed handle for fresh posts, not a free-text news query. The
followed-source pipeline (Phase 5d SP3) calls
:meth:`XAccountAdapter.fetch_new_items` (handle + ``since`` cutoff), not
:meth:`search`. The base ``search()`` contract is still honoured — ``search``
treats its ``search_query`` as a handle and delegates — so the adapter is a
drop-in ``BaseNewsAdapter``.

Locked decisions (plans/phase-5d-source-ingestion.md, owner 2026-06-17):
  • **Discovery = xAI / Grok Agent Tools API** (``XAI_API_KEY``, the ``/v1/responses``
    endpoint with the server-side ``x_search`` tool): Grok itself searches X — scoped
    to the followed handle via ``allowed_x_handles`` + ``from_date`` — and synthesizes
    the handle's recent substantive posts with links. (Supersedes the deprecated Live
    Search ``search_parameters`` API, which now returns HTTP 410.) The xAI HTTP call
    is behind an **injectable seam** (``post_discoverer``) so tests mock it — no live call.
  • **Image = a screenshot of the tweet** (``tweet_screenshot.render_tweet_screenshot``,
    Playwright headless Chromium), NOT a generated poster. The screenshot renderer
    is itself seam-mockable.
  • On rate-limit / no-auth / render failure the adapter returns a clean ``failed``
    status (an empty candidate list + a loud structured error log with
    ``fix_suggestion``) — it never crashes the batch and never leaks ``XAI_API_KEY``.

Emitted ``CandidateStory`` shape (per post):
  • ``candidate_external_id`` / ``candidate_url`` = the canonical tweet URL.
  • ``candidate_title``            = ``@handle`` + a short snippet of the post text.
  • ``candidate_outlet_domain``    = ``"x.com"``.
  • ``candidate_outlet_name``      = ``"@handle"``.
  • ``candidate_social_image_url`` = the rendered tweet-screenshot path.
  • ``candidate_body_text``        = the full post text (filled by ``extract_body``).
  • ``candidate_platform_metadata``= a :class:`TwitterContentMetadata` dump.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.adapters.x_resolver import parse_x_handle
from agents.ingestion.models import CandidateStory, TwitterContentMetadata
from agents.ingestion.tweet_screenshot import (
    TweetPageRenderer,
    parse_tweet_id,
    render_tweet_screenshot,
)
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger(__name__)

_ADAPTER_NAME = "x_account"
_OUTLET_DOMAIN = "x.com"
_XAI_BASE_URL = "https://api.x.ai/v1"
_XAI_MODEL = "grok-4.3"  # Agent Tools API model (server-side x_search); see /v1/responses
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_POSTS = 10
_TITLE_SNIPPET_CHARS = 80

# An async discoverer that, given a handle + since cutoff + max_posts, returns the
# raw post dicts the xAI x_search call surfaced. Injected so the xAI HTTP call is
# fully mockable (no live call in tests) and the key is read only inside it.
PostDiscoverer = Callable[[str, datetime, int], Awaitable[list[dict[str, Any]]]]


class XAccountAdapter(BaseNewsAdapter):
    """Source-keyed X adapter: xAI/Grok discovery + Playwright tweet screenshot.

    Discovers a followed handle's recent substantive posts via xAI Live Search,
    normalizes each into a :class:`CandidateStory`, and renders a tweet-card
    screenshot as the reel image. A discovery failure (rate-limit / no-auth /
    network) returns an empty list (clean ``failed``), never raises past
    :meth:`fetch_new_items`, so one bad handle cannot abort the user's ingestion.

    Attributes:
        post_discoverer: Injected async xAI discovery callable (mocked in tests).
        screenshot_renderer: Injected async ``(tweet_url) -> path|None`` renderer;
            defaults to the real Playwright-backed ``render_tweet_screenshot``.
        max_posts: Maximum recent posts to request per fetch.

    Example:
        >>> adapter = XAccountAdapter()
        >>> # candidates = await adapter.fetch_new_items("Reuters", since_utc=...)
    """

    def __init__(
        self,
        post_discoverer: PostDiscoverer | None = None,
        screenshot_renderer: "ScreenshotRenderer | None" = None,
        page_renderer: TweetPageRenderer | None = None,
        settings: Settings | None = None,
        max_posts: int = _DEFAULT_MAX_POSTS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Build the adapter.

        Args:
            post_discoverer: Optional async ``(handle, since, max_posts) -> list[dict]``
                xAI discovery callable. When None, the real xAI Live Search seam is
                used (reads ``XAI_API_KEY`` from settings at call time). Injected by
                tests to mock xAI.
            screenshot_renderer: Optional async ``(tweet_url) -> str|None`` renderer.
                When None, defaults to ``tweet_screenshot.render_tweet_screenshot``
                (which itself takes the injectable Playwright ``page_renderer``).
            page_renderer: Optional Playwright seam forwarded to the default
                screenshot renderer (ignored when ``screenshot_renderer`` is set).
            settings: Optional Settings (for ``xai_api_key``); built from env if None.
            max_posts: Maximum recent posts to request per fetch.
            timeout_seconds: HTTP timeout for the xAI call.
        """
        self._settings = settings or Settings()
        self.max_posts = max_posts
        self.timeout_seconds = timeout_seconds
        self._page_renderer = page_renderer
        # Reason: dependency injection of the xAI call + screenshot keeps the
        # adapter testable without the network, a browser, or the API key.
        self._post_discoverer = post_discoverer or self._default_xai_discoverer
        self._screenshot_renderer = screenshot_renderer or self._default_screenshot

    # ------------------------------------------------------------------
    # Source-keyed entry point (the real path — called by source_pipeline, SP3)
    # ------------------------------------------------------------------

    async def fetch_new_items(
        self,
        source_external_id: str,
        since_utc: datetime,
        **kwargs: Any,
    ) -> list[CandidateStory]:
        """Discover a handle's recent posts since a cutoff and return enriched candidates.

        Asks xAI Live Search for the handle's recent substantive posts, keeps those
        published strictly after ``since_utc``, normalizes each into a
        :class:`CandidateStory` (body text + ``TwitterContentMetadata``), and renders
        a tweet-card screenshot for each as its social image. A discovery failure
        (rate-limit / no-auth / network) is caught and returns an empty list (clean
        ``failed``) — never raised, so one bad handle cannot fail the user's batch.

        Args:
            source_external_id: The X handle (with or without ``@``, or a profile URL).
            since_utc: Only return posts published strictly after this UTC time.
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            CandidateStory items for new posts (body + screenshot set). Empty on a
            discovery failure or when there are no new posts.

        Example:
            >>> adapter = XAccountAdapter()
            >>> # items = await adapter.fetch_new_items("@Reuters", datetime(2026, 6, 1))
        """
        try:
            handle = parse_x_handle(source_external_id)
        except Exception as exc:  # noqa: BLE001 — a bad handle is a clean skip, not a crash
            logger.error(
                "x_account_bad_handle",
                adapter=_ADAPTER_NAME,
                raw_input=str(source_external_id)[:100],
                error_message=str(exc)[:200],
                fix_suggestion="Pass a valid X handle (content_sources.external_id) "
                "like 'Reuters' or an x.com profile URL; handle skipped",
            )
            return []

        since = (
            since_utc if since_utc.tzinfo else since_utc.replace(tzinfo=timezone.utc)
        )
        logger.info(
            "x_account_fetch_started",
            adapter=_ADAPTER_NAME,
            handle=handle,
            since_utc=since.isoformat(),
        )

        try:
            raw_posts = await self._post_discoverer(handle, since, self.max_posts)
        except Exception as exc:  # noqa: BLE001 — boundary: discovery failure → clean failed
            logger.error(
                "x_account_discovery_failed",
                adapter=_ADAPTER_NAME,
                handle=handle,
                error_type=type(exc).__name__,
                error_message=str(exc)[:300],
                fix_suggestion="xAI discovery failed (rate-limit / no-auth / network); "
                "handle returned empty. Verify XAI_API_KEY is set, has quota, and "
                "the worker has outbound network to api.x.ai",
            )
            return []

        candidates: list[CandidateStory] = []
        skipped_no_url = 0
        skipped_old = 0
        for raw_post in raw_posts:
            candidate = self._raw_post_to_candidate(raw_post, handle)
            if candidate is None:
                skipped_no_url += 1
                continue
            if (
                candidate.candidate_published_utc
                and candidate.candidate_published_utc <= since
            ):
                skipped_old += 1
                continue
            enriched = await self.extract_body(candidate)
            candidates.append(enriched)

        logger.info(
            "x_account_fetch_completed",
            adapter=_ADAPTER_NAME,
            handle=handle,
            posts_returned=len(candidates),
            skipped_no_url=skipped_no_url,
            skipped_old=skipped_old,
        )
        return candidates

    # ------------------------------------------------------------------
    # BaseNewsAdapter contract
    # ------------------------------------------------------------------

    async def search(
        self,
        search_query: str,
        since_utc: datetime,
        **kwargs: Any,
    ) -> list[CandidateStory]:
        """Base-contract shim: ``search_query`` is treated as an X handle.

        X ingestion is source-keyed, not free-text-query-keyed, so ``search``
        interprets ``search_query`` as a handle and delegates to
        :meth:`fetch_new_items` (the real entry point the source pipeline uses).

        Args:
            search_query: The X handle (or profile URL).
            since_utc: Only return posts published after this UTC time.
            **kwargs: Forwarded to :meth:`fetch_new_items`.

        Returns:
            New posts as CandidateStory items (see fetch_new_items).
        """
        return await self.fetch_new_items(search_query, since_utc, **kwargs)

    async def extract_body(
        self,
        candidate: CandidateStory,
        **kwargs: Any,
    ) -> CandidateStory:
        """Render the tweet screenshot and set it as the candidate's social image.

        The post text is already on ``candidate_body_text`` from discovery; this
        method's job (the ``extract_body`` enrichment phase) is to render the tweet
        card screenshot and stamp its path onto ``candidate_social_image_url``. A
        render failure leaves the image None (logged loud in the renderer) and is
        never raised — a missing screenshot must not fail the post.

        Args:
            candidate: The discovery-built candidate (body already set).
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            The candidate with ``candidate_social_image_url`` set to the screenshot
            path on success, else left None.
        """
        screenshot_path = await self._screenshot_renderer(candidate.candidate_url)
        if screenshot_path:
            candidate.candidate_social_image_url = screenshot_path
            logger.info(
                "x_account_extract_body_success",
                adapter=_ADAPTER_NAME,
                tweet_url=candidate.candidate_url,
                has_image=True,
            )
        else:
            logger.warning(
                "x_account_screenshot_missing",
                adapter=_ADAPTER_NAME,
                tweet_url=candidate.candidate_url,
                fix_suggestion="Tweet screenshot did not render; the post is kept "
                "without an image (see tweet_screenshot logs for the cause)",
            )
        return candidate

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_post_to_candidate(
        self, raw_post: dict[str, Any], handle: str
    ) -> CandidateStory | None:
        """Map one discovered post dict into a CandidateStory, or None if unusable.

        A usable post needs a canonical tweet URL (the external id / dedup key) and
        non-empty text. Posts missing either are dropped (the caller counts them).

        Args:
            raw_post: One post dict from the discoverer (``tweet_url``, ``text``,
                optional ``published_utc`` / ``is_quote`` / ``quoted_tweet_url`` /
                ``is_thread``).
            handle: The canonical author handle (without ``@``).

        Returns:
            A CandidateStory with body + platform metadata set, or None.
        """
        tweet_url = str(raw_post.get("tweet_url") or "").strip()
        text = str(raw_post.get("text") or "").strip()
        if not tweet_url or not text:
            return None

        tweet_id = parse_tweet_id(tweet_url) or tweet_url
        published_utc = _parse_post_datetime(raw_post.get("published_utc"))

        metadata = TwitterContentMetadata(
            tweet_id=tweet_id,
            author_handle=handle,
            tweet_url=tweet_url,
            is_quote=bool(raw_post.get("is_quote", False)),
            quoted_tweet_url=(raw_post.get("quoted_tweet_url") or None),
            is_thread=bool(raw_post.get("is_thread", False)),
        )

        snippet = text[:_TITLE_SNIPPET_CHARS].strip()
        if len(text) > _TITLE_SNIPPET_CHARS:
            snippet = f"{snippet}…"

        return CandidateStory(
            candidate_external_id=tweet_url,
            candidate_title=f"@{handle}: {snippet}",
            candidate_url=tweet_url,
            candidate_outlet_domain=_OUTLET_DOMAIN,
            candidate_outlet_name=f"@{handle}",
            candidate_published_utc=published_utc or datetime.now(timezone.utc),
            candidate_body_text=text,
            candidate_platform_metadata=metadata.model_dump(),
        )

    async def _default_screenshot(self, tweet_url: str) -> str | None:
        """Default screenshot renderer: delegate to ``render_tweet_screenshot``.

        Forwards the injectable Playwright ``page_renderer`` so the screenshot path
        stays seam-mockable end to end.
        """
        return await render_tweet_screenshot(
            tweet_url, page_renderer=self._page_renderer
        )

    async def _default_xai_discoverer(
        self, handle: str, since_utc: datetime, max_posts: int
    ) -> list[dict[str, Any]]:
        """Real xAI/Grok Agent Tools seam: discover a handle's recent posts via x_search.

        Imported lazily (httpx is already a dep; the import is local only to keep
        the call boundary explicit). Reads ``XAI_API_KEY`` from settings at call
        time and sends it ONLY in the Authorization header — it is never logged.
        Calls the ``/v1/responses`` endpoint with the server-side ``x_search`` tool
        scoped to this handle (``allowed_x_handles`` + ``from_date``): Grok runs the
        X search itself and synthesizes the handle's recent posts, which we ask it to
        return as a strict JSON array. A missing key or HTTP error raises
        ``AdapterFetchError``, which :meth:`fetch_new_items` catches into a clean
        ``failed``.

        Args:
            handle: The canonical author handle (without ``@``).
            since_utc: The cutoff; its date scopes ``x_search`` via ``from_date``.
            max_posts: Maximum posts to request.

        Returns:
            A list of post dicts (``tweet_url``, ``text``, ``published_utc``, ...).

        Raises:
            AdapterFetchError: When ``XAI_API_KEY`` is unset, or the xAI call
                rate-limits / errors / returns an unparseable body (no key leaked).
        """
        import httpx  # noqa: PLC0415 — local import keeps the network boundary explicit

        api_key = self._settings.xai_api_key.get_secret_value().strip()
        if not api_key:
            raise AdapterFetchError(
                message="XAI_API_KEY is not set",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Set XAI_API_KEY in the worker env (it is in .env); "
                "the X adapter needs it for xAI Agent Tools (x_search) discovery",
            )

        prompt = _build_discovery_prompt(handle, since_utc, max_posts)
        # Reason: the server-side x_search tool scoped to this one handle is what
        # actually fetches the posts; from_date narrows the search to the cutoff day.
        x_search_tool: dict[str, Any] = {
            "type": "x_search",
            "allowed_x_handles": [handle],
            "from_date": since_utc.date().isoformat(),
        }
        payload = {
            "model": _XAI_MODEL,
            "input": [{"role": "user", "content": prompt}],
            "tools": [x_search_tool],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{_XAI_BASE_URL}/responses",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            # Reason: surface auth (401/403) and rate-limit (429) distinctly, but
            # NEVER include the request headers / key in the log or error.
            status_code = exc.response.status_code
            raise AdapterFetchError(
                message=f"xAI x_search HTTP {status_code} for @{handle}",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion=(
                    "xAI auth failed — verify XAI_API_KEY is valid and authorized"
                    if status_code in (401, 403)
                    else "xAI rate-limited — back off and retry"
                    if status_code == 429
                    else "xAI returned an HTTP error; check api.x.ai status and that "
                    "the Agent Tools (/v1/responses + x_search) API is enabled"
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001 — normalize all transport errors
            raise AdapterFetchError(
                message=f"xAI x_search call failed for @{handle}",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="xAI call errored (network/timeout/parse); retry later",
            ) from exc

        return _parse_xai_response(body)


# An async callable that renders a tweet URL into a screenshot path (or None).
ScreenshotRenderer = Callable[[str], Awaitable[str | None]]


# ----------------------------------------------------------------------
# Module-level pure helpers (prompt + response parsing + datetime)
# ----------------------------------------------------------------------


def _build_discovery_prompt(handle: str, since_utc: datetime, max_posts: int) -> str:
    """Build the Grok x_search prompt asking for a handle's recent posts as JSON.

    The ``x_search`` tool is already scoped to ``@handle`` (``allowed_x_handles``),
    so this prompt asks the natural question — has the account posted, and what +
    the link — but pins the *output* to a strict JSON array so the pipeline can
    parse it deterministically (CLAUDE.md Rule 5: code parses, model judges).

    Args:
        handle: The author handle (without ``@``).
        since_utc: The recency cutoff to bias toward.
        max_posts: Maximum posts to request.

    Returns:
        A prompt instructing Grok to return ONLY a strict JSON array of posts.
    """
    return (
        f"Using x_search, check the X/Twitter account @{handle}: has it posted "
        f"anything since {since_utc.isoformat()}? If yes, what did it post and what "
        f"is the link to each post? Return up to the {max_posts} most recent "
        "substantive posts; exclude pure retweets and one-word replies. "
        "Respond with ONLY a JSON array (no prose, no markdown fences). Each "
        "element must be an object with keys: "
        '"tweet_url" (the canonical https://x.com/<handle>/status/<id> URL), '
        '"text" (the full post text), '
        '"published_utc" (ISO-8601 UTC timestamp), '
        '"is_quote" (boolean), "quoted_tweet_url" (string or null), '
        '"is_thread" (boolean). '
        "If it has not posted anything, respond with an empty array []."
    )


def _extract_assistant_text(body: dict[str, Any]) -> str | None:
    """Pull the assistant's text out of an xAI response, across both API shapes.

    Primary: the Agent Tools ``/v1/responses`` shape — ``body["output"]`` is a list
    whose ``type == "message"`` item carries a ``content`` list with a
    ``type == "output_text"`` object holding ``text``. Fallback: the legacy
    chat-completions shape ``body["choices"][0]["message"]["content"]``.

    Args:
        body: The parsed xAI JSON response.

    Returns:
        The assistant text, or None when neither shape is present.
    """
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    return content.get("text")

    # Reason: tolerate the legacy chat-completions shape so older fixtures / a
    # fallback transport still parse rather than silently dropping posts.
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_xai_response(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the post array out of an xAI response, or [].

    The assistant text is expected to be a JSON array string (per the prompt).
    Tolerates an accidental ```json fence. A non-array / unparseable payload yields
    [] (logged), not a crash — the caller treats it as no posts.

    Args:
        body: The parsed xAI JSON response (``/v1/responses`` or chat-completions).

    Returns:
        The list of post dicts, or an empty list when none could be parsed.
    """
    content = _extract_assistant_text(body)
    if content is None:
        logger.warning(
            "x_account_xai_no_content",
            adapter=_ADAPTER_NAME,
            fix_suggestion="xAI response had no message content; treated as no posts",
        )
        return []

    text = (content or "").strip()
    if text.startswith("```"):
        # Strip a ```json ... ``` fence if the model added one despite instructions.
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "x_account_xai_unparseable",
            adapter=_ADAPTER_NAME,
            content_preview=text[:160],
            fix_suggestion="xAI did not return a JSON array; treated as no posts",
        )
        return []

    if not isinstance(parsed, list):
        logger.warning(
            "x_account_xai_not_array",
            adapter=_ADAPTER_NAME,
            fix_suggestion="xAI JSON was not an array; treated as no posts",
        )
        return []

    return [item for item in parsed if isinstance(item, dict)]


def _parse_post_datetime(value: Any) -> datetime | None:
    """Parse a post's ISO-8601 ``published_utc`` into a UTC-aware datetime, or None.

    Naive values are assumed UTC; a trailing ``Z`` is normalized. A missing /
    unparseable value returns None (the caller falls back to "now" so the post is
    not wrongly filtered out by the cutoff).

    Args:
        value: The raw ``published_utc`` from a discovered post.

    Returns:
        A UTC-aware datetime, or None.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
