"""YouTube source adapter — channel-RSS upload detection + yt-dlp transcripts.

Unlike the interest-keyed news adapters (``gdelt_doc.py``), YouTube ingestion is
**source-keyed**: it polls one followed channel's RSS feed for fresh uploads, not
a free-text news query. The followed-source pipeline (Phase 5d SP3) therefore
calls :meth:`YouTubeAdapter.fetch_new_items` (channel id + ``since`` cutoff), not
:meth:`search`. The base ``search()`` contract is still honoured coherently —
``search`` treats its ``search_query`` argument as a channel ``external_id`` and
delegates to ``fetch_new_items`` — so the adapter is a drop-in ``BaseNewsAdapter``.

Locked decisions (plans/phase-5d-source-ingestion.md, owner 2026-06-17):
  • **Upload detection = channel RSS** (``youtube.com/feeds/videos.xml?channel_id=<id>``):
    keyless, returns the latest ~15 videos + ``media:thumbnail``. **No YouTube Data API key.**
  • **Transcript = yt-dlp** (auto-subs / uploaded captions, vtt). Caption-less
    videos are returned ``failed`` and skipped — never crash the batch.
  • **Image = the video thumbnail** (best ``media:thumbnail`` / yt-dlp metadata),
    NOT a generated poster (the poster stage skips generation for source items).

Emitted ``CandidateStory`` shape:
  • ``candidate_external_id`` / ``candidate_url`` = the canonical watch URL.
  • ``candidate_title``           = the video title.
  • ``candidate_outlet_domain``   = ``"youtube.com"``.
  • ``candidate_outlet_name``     = the channel name.
  • ``candidate_social_image_url``= the best thumbnail URL.
  • ``candidate_body_text``       = the transcript (filled by ``extract_body``).

All external calls (RSS HTTP fetch, yt-dlp) are mockable at the boundary for tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import httpx

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.models import CandidateStory
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

_ADAPTER_NAME = "youtube"
_RSS_FEED_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_WATCH_URL_TEMPLATE = "https://www.youtube.com/watch?v={video_id}"
_OUTLET_DOMAIN = "youtube.com"
_USER_AGENT = "News20/1.0 (source ingestion)"
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Reason: the Atom/Media RSS namespaces YouTube's channel feed uses. ElementTree
# needs the full namespace URIs to address ``entry``/``yt:videoId``/``media:*``.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# yt-dlp options: probe metadata + caption availability only, never download media.
_YTDLP_BASE_OPTS: dict[str, Any] = {
    "skip_download": True,
    "writeautomaticsub": True,
    "writesubtitles": True,
    "subtitleslangs": ["en", "en-US", "en-GB"],
    "subtitlesformat": "vtt",
    "quiet": True,
    "no_warnings": True,
}


class CaptionUnavailableError(AdapterFetchError):
    """Raised internally when a video has no usable captions (auto or uploaded).

    The video is then skipped (returned ``failed``) without aborting the batch —
    caption-less uploads are expected and must not crash a channel's ingestion.

    Attributes:
        video_url: The watch URL whose captions could not be fetched.
    """

    def __init__(
        self,
        video_url: str,
        fix_suggestion: str = "Video has no auto/uploaded captions; skip it "
        "(or add an audio-transcription fallback such as Whisper later)",
    ) -> None:
        self.video_url = video_url
        super().__init__(
            message=f"no usable captions for {video_url}",
            adapter_name=_ADAPTER_NAME,
            fix_suggestion=fix_suggestion,
        )


class YouTubeAdapter(BaseNewsAdapter):
    """Source-keyed YouTube adapter: channel RSS detect + yt-dlp transcript + thumbnail.

    Detects fresh uploads from one channel via its keyless RSS feed, keeps videos
    published after the ``since`` cutoff, and enriches each with a transcript and
    canonical thumbnail via yt-dlp. Caption-less videos are skipped (returned
    ``failed``) so one un-captioned upload cannot fail the whole channel batch.

    Attributes:
        http_client: Optional injected httpx client (the RSS fetcher). When None,
            a client is created per RSS fetch and closed after — tests inject a mock.
        timeout_seconds: HTTP timeout for the RSS fetch.

    Example:
        >>> adapter = YouTubeAdapter()
        >>> # candidates = await adapter.fetch_new_items("UCxxxx", since_utc=...)
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        ytdlp_extractor: "Any | None" = None,
    ) -> None:
        """Build the adapter.

        Args:
            http_client: Optional shared httpx.AsyncClient for the RSS fetch. When
                None, one is created per fetch and closed after.
            timeout_seconds: HTTP timeout (seconds) for the RSS fetch.
            ytdlp_extractor: Optional callable ``(video_url: str) -> dict`` that
                returns yt-dlp's ``extract_info`` dict for a video. Injected by
                tests to mock yt-dlp; defaults to the real yt-dlp extractor.
        """
        self._http_client = http_client
        self.timeout_seconds = timeout_seconds
        # Reason: dependency injection of the yt-dlp call keeps the adapter
        # testable without the network and without importing yt-dlp at module load.
        self._ytdlp_extractor = ytdlp_extractor or _default_ytdlp_extract_info

    # ------------------------------------------------------------------
    # Source-keyed entry point (the real path — called by source_pipeline, SP3)
    # ------------------------------------------------------------------

    async def fetch_new_items(
        self,
        source_external_id: str,
        since_utc: datetime,
        **kwargs: Any,
    ) -> list[CandidateStory]:
        """Detect a channel's new uploads since a cutoff and return enriched candidates.

        Reads the channel RSS feed, keeps videos published strictly after
        ``since_utc``, and for each fetches the transcript + best thumbnail via
        yt-dlp. Caption-less videos are skipped (logged + dropped), never raised
        past this method, so one un-captioned upload cannot fail the channel batch.

        Args:
            source_external_id: The YouTube channel id (the RSS ``channel_id``).
            since_utc: Only return videos published strictly after this UTC time.
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            CandidateStory items for new, captioned uploads (body + thumbnail set).
            Caption-less / un-enrichable videos are omitted.

        Raises:
            AdapterFetchError: When the RSS feed itself cannot be fetched or parsed
                (the channel cannot be polled at all). Per-video failures do NOT
                raise — they are skipped. The caller catches this per-source so one
                bad channel does not abort the whole user's ingestion.

        Example:
            >>> adapter = YouTubeAdapter()
            >>> # items = await adapter.fetch_new_items("UCabc", datetime(2026, 6, 1))
        """
        channel_id = source_external_id.strip()
        if not channel_id:
            raise AdapterFetchError(
                message="empty channel external_id",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Pass the YouTube channel_id (content_sources.external_id)",
            )

        since = (
            since_utc if since_utc.tzinfo else since_utc.replace(tzinfo=timezone.utc)
        )
        logger.info(
            "youtube_fetch_started",
            channel_id=channel_id,
            since_utc=since.isoformat(),
        )

        feed_xml = await self._fetch_channel_feed(channel_id)
        entries = self._parse_feed_entries(feed_xml, channel_id)
        new_entries = [e for e in entries if e["published_utc"] > since]

        logger.info(
            "youtube_feed_parsed",
            channel_id=channel_id,
            entries_total=len(entries),
            entries_new=len(new_entries),
        )

        candidates: list[CandidateStory] = []
        skipped_no_captions = 0
        skipped_failed = 0
        for entry in new_entries:
            candidate = self._entry_to_candidate(entry)
            try:
                enriched = await self.extract_body(candidate)
            except CaptionUnavailableError:
                skipped_no_captions += 1
                continue
            if enriched.candidate_body_text:
                candidates.append(enriched)
            else:
                # extract_body swallowed a non-caption failure (returned no body).
                skipped_failed += 1

        logger.info(
            "youtube_fetch_completed",
            channel_id=channel_id,
            candidates_returned=len(candidates),
            skipped_no_captions=skipped_no_captions,
            skipped_failed=skipped_failed,
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
        """Base-contract shim: ``search_query`` is treated as a channel external_id.

        YouTube ingestion is source-keyed, not free-text-query-keyed, so there is
        no meaningful "news query" path. To stay a coherent ``BaseNewsAdapter``,
        ``search`` interprets ``search_query`` as a channel id and delegates to
        :meth:`fetch_new_items` (the real entry point the source pipeline uses).

        Args:
            search_query: The YouTube channel id (RSS ``channel_id``).
            since_utc: Only return videos published after this UTC time.
            **kwargs: Forwarded to :meth:`fetch_new_items`.

        Returns:
            New, captioned uploads as CandidateStory items (see fetch_new_items).
        """
        return await self.fetch_new_items(search_query, since_utc, **kwargs)

    async def extract_body(
        self,
        candidate: CandidateStory,
        **kwargs: Any,
    ) -> CandidateStory:
        """Fill ``candidate_body_text`` with the video transcript via yt-dlp.

        Runs yt-dlp's (synchronous) ``extract_info`` off the event loop to pull
        the video's auto/uploaded captions and best thumbnail. A caption-less
        video raises :class:`CaptionUnavailableError` (the caller skips it). Any
        other extraction failure is swallowed (body left None, logged loud) so a
        single transient error cannot fail the channel batch.

        Args:
            candidate: The metadata-only candidate to enrich (body is None).
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            The candidate with ``candidate_body_text`` (and a refreshed thumbnail)
            populated.

        Raises:
            CaptionUnavailableError: When the video has no usable captions — the
                signal for the caller to skip this video as ``failed``.
        """
        video_url = candidate.candidate_url
        try:
            info = await asyncio.to_thread(self._ytdlp_extractor, video_url)
        except CaptionUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad video must not fail the batch
            logger.warning(
                "youtube_extract_body_failed",
                video_url=video_url,
                error_type=type(exc).__name__,
                error_message=str(exc)[:300],
                fix_suggestion="yt-dlp metadata fetch failed; candidate skipped (kept without body)",
            )
            return candidate

        transcript = _extract_transcript_text(info)
        if not transcript:
            logger.warning(
                "youtube_no_captions",
                video_url=video_url,
                fix_suggestion="No auto/uploaded captions; video skipped as failed",
            )
            raise CaptionUnavailableError(video_url=video_url)

        candidate.candidate_body_text = transcript
        # Reason: yt-dlp's metadata thumbnail is higher-fidelity than the RSS one;
        # prefer it when present, else keep the RSS thumbnail already on the candidate.
        best_thumbnail = _extract_best_thumbnail(info)
        if best_thumbnail:
            candidate.candidate_social_image_url = best_thumbnail
        logger.info(
            "youtube_extract_body_success",
            video_url=video_url,
            transcript_chars=len(transcript),
            has_thumbnail=candidate.candidate_social_image_url is not None,
        )
        return candidate

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_channel_feed(self, channel_id: str) -> str:
        """GET the channel RSS feed XML (raises AdapterFetchError on any failure)."""
        feed_url = _RSS_FEED_TEMPLATE.format(channel_id=channel_id)
        owns_client = self._http_client is None
        client = self._http_client
        try:
            if client is None:
                client = httpx.AsyncClient(
                    timeout=self.timeout_seconds, follow_redirects=True
                )
            response = await client.get(feed_url, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 — normalize to a typed adapter error
            logger.warning(
                "youtube_rss_fetch_failed",
                channel_id=channel_id,
                error_type=type(exc).__name__,
                error_message=str(exc)[:300],
                fix_suggestion="Channel RSS fetch failed; verify the channel_id and network",
            )
            raise AdapterFetchError(
                message=f"failed to fetch channel RSS feed for {channel_id}",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Verify the channel_id is a valid YouTube channel and the worker has network access",
            ) from exc
        finally:
            if owns_client and client is not None:
                await client.aclose()

    def _parse_feed_entries(
        self, feed_xml: str, channel_id: str
    ) -> list[dict[str, Any]]:
        """Parse the channel RSS feed into a list of normalized video-entry dicts.

        Raises AdapterFetchError on malformed XML (the channel cannot be polled).
        Entries missing a video id or publish time are skipped (logged), not fatal.

        Returns:
            One dict per usable entry with keys: ``video_id``, ``video_url``,
            ``title``, ``published_utc``, ``channel_name``, ``thumbnail_url``.
        """
        try:
            root = ElementTree.fromstring(feed_xml)
        except ElementTree.ParseError as exc:
            logger.warning(
                "youtube_feed_parse_failed",
                channel_id=channel_id,
                error_message=str(exc)[:300],
                fix_suggestion="Channel RSS returned non-XML; verify the channel_id",
            )
            raise AdapterFetchError(
                message=f"channel RSS feed is not valid XML for {channel_id}",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Verify the channel_id resolves to a real channel feed",
            ) from exc

        channel_name = _feed_channel_name(root)
        entries: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", _NS):
            parsed = _parse_entry(entry, channel_name)
            if parsed is not None:
                entries.append(parsed)
        return entries

    def _entry_to_candidate(self, entry: dict[str, Any]) -> CandidateStory:
        """Map a parsed feed entry into a metadata-only CandidateStory."""
        return CandidateStory(
            candidate_external_id=entry["video_url"],
            candidate_title=entry["title"],
            candidate_url=entry["video_url"],
            candidate_outlet_domain=_OUTLET_DOMAIN,
            candidate_outlet_name=entry["channel_name"],
            candidate_published_utc=entry["published_utc"],
            candidate_social_image_url=entry["thumbnail_url"],
        )


# ----------------------------------------------------------------------
# Module-level pure helpers (parsing + yt-dlp normalization)
# ----------------------------------------------------------------------


def _feed_channel_name(root: ElementTree.Element) -> str:
    """Read the channel display name from the feed's top-level ``<title>``."""
    title_el = root.find("atom:title", _NS)
    if title_el is not None and title_el.text and title_el.text.strip():
        return title_el.text.strip()
    return "YouTube"


def _parse_entry(
    entry: ElementTree.Element, channel_name: str
) -> dict[str, Any] | None:
    """Normalize one ``<entry>`` into a video dict, or None if unusable.

    A usable entry needs a video id (→ canonical watch URL) and a publish time.
    The thumbnail comes from ``media:group/media:thumbnail@url`` when present.

    Args:
        entry: An Atom ``<entry>`` element from the channel feed.
        channel_name: The channel display name (stamped onto the entry).

    Returns:
        A dict with video_id/video_url/title/published_utc/channel_name/thumbnail_url,
        or None when the entry lacks a video id or a parseable publish time.
    """
    video_id_el = entry.find("yt:videoId", _NS)
    video_id = (video_id_el.text or "").strip() if video_id_el is not None else ""
    if not video_id:
        return None

    published_el = entry.find("atom:published", _NS)
    published_utc = _parse_rss_datetime(
        published_el.text if published_el is not None else None
    )
    if published_utc is None:
        return None

    title_el = entry.find("atom:title", _NS)
    title = (title_el.text or "").strip() if title_el is not None else ""
    if not title:
        title = f"YouTube video {video_id}"

    return {
        "video_id": video_id,
        "video_url": _WATCH_URL_TEMPLATE.format(video_id=video_id),
        "title": title,
        "published_utc": published_utc,
        "channel_name": channel_name,
        "thumbnail_url": _entry_thumbnail_url(entry),
    }


def _entry_thumbnail_url(entry: ElementTree.Element) -> str | None:
    """Read the ``media:thumbnail@url`` from a feed entry, or None if absent."""
    thumb = entry.find("media:group/media:thumbnail", _NS)
    if thumb is None:
        thumb = entry.find("media:thumbnail", _NS)
    if thumb is not None:
        url = (thumb.get("url") or "").strip()
        return url or None
    return None


def _parse_rss_datetime(value: str | None) -> datetime | None:
    """Parse an RSS/Atom ISO-8601 timestamp into a UTC-aware datetime, or None.

    YouTube feeds use e.g. ``2026-06-15T10:15:00+00:00``. A trailing ``Z`` is
    normalized to ``+00:00`` for ``datetime.fromisoformat``. Naive values are
    assumed UTC. Returns None on an empty / unparseable value (the entry is then
    skipped rather than crashing the feed parse).
    """
    if not value or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning(
            "youtube_bad_published_date",
            raw_value=value[:60],
            fix_suggestion="Expected ISO-8601 publish time; entry skipped",
        )
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _extract_transcript_text(info: dict[str, Any]) -> str | None:
    """Pull a plain-text transcript from a yt-dlp ``extract_info`` dict, or None.

    yt-dlp surfaces captions under ``subtitles`` (uploaded) and
    ``automatic_captions`` (auto-generated), keyed by language → list of formats.
    A caption format that carries inline ``data`` (the vtt text) is parsed to
    plain text. Uploaded captions are preferred over auto captions; English
    variants are preferred over other languages. Returns None when no caption
    track carries usable text — the caller treats that as caption-less.

    Args:
        info: The yt-dlp ``extract_info`` result dict for one video.

    Returns:
        The de-duplicated transcript text, or None if no captions carry text.
    """
    for track_key in ("subtitles", "automatic_captions"):
        tracks = info.get(track_key) or {}
        if not isinstance(tracks, dict):
            continue
        for lang in _preferred_caption_langs(tracks):
            for fmt in tracks.get(lang) or []:
                vtt_text = fmt.get("data") if isinstance(fmt, dict) else None
                if vtt_text and vtt_text.strip():
                    plain = _vtt_to_plain_text(vtt_text)
                    if plain:
                        return plain
    return None


def _preferred_caption_langs(tracks: dict[str, Any]) -> list[str]:
    """Order a caption track's language keys: English variants first, then the rest."""
    langs = list(tracks.keys())
    english = [lang for lang in langs if lang.lower().startswith("en")]
    other = [lang for lang in langs if not lang.lower().startswith("en")]
    return english + other


def _vtt_to_plain_text(vtt_text: str) -> str:
    """Strip WebVTT markup/timing into de-duplicated plain transcript text.

    Drops the ``WEBVTT`` header, cue-timing lines (``00:00:01.000 --> ...``),
    cue index numbers, and inline ``<...>`` tags, then collapses consecutive
    duplicate lines (auto-captions repeat each line as it scrolls).

    Args:
        vtt_text: Raw WebVTT caption text.

    Returns:
        Plain transcript text (may be empty if the vtt carried no spoken lines).

    Example:
        >>> _vtt_to_plain_text("WEBVTT\\n\\n00:00.000 --> 00:01.000\\nhello\\nhello")
        'hello'
    """
    import re

    lines: list[str] = []
    for raw_line in vtt_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line:
            continue
        if line.isdigit():  # cue index
            continue
        # Strip inline timing/positioning tags like <00:00:01.000> and <c>...</c>.
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if not clean:
            continue
        # Collapse consecutive duplicates (auto-caption scroll repeats).
        if lines and lines[-1] == clean:
            continue
        lines.append(clean)
    return " ".join(lines).strip()


def _extract_best_thumbnail(info: dict[str, Any]) -> str | None:
    """Pick the best thumbnail URL from a yt-dlp ``extract_info`` dict, or None.

    Prefers the top-level ``thumbnail``; else the highest-resolution entry in the
    ``thumbnails`` list (by width). Returns None when no thumbnail is present.

    Args:
        info: The yt-dlp ``extract_info`` result dict for one video.

    Returns:
        The best thumbnail URL, or None.
    """
    top = info.get("thumbnail")
    if isinstance(top, str) and top.strip():
        return top.strip()
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list) and thumbnails:
        best = max(
            (t for t in thumbnails if isinstance(t, dict) and t.get("url")),
            key=lambda t: t.get("width") or 0,
            default=None,
        )
        if best:
            url = (best.get("url") or "").strip()
            return url or None
    return None


def _default_ytdlp_extract_info(video_url: str) -> dict[str, Any]:
    """Real yt-dlp extractor: fetch metadata + caption text for one video.

    Imported lazily so the module loads (and tests run) without yt-dlp installed,
    and so the dependency is only required at actual fetch time on the worker.

    yt-dlp's ``extract_info`` exposes caption tracks as ``{lang: [{ext, url, ...}]}``
    with a downloadable ``url`` (not inline text). To keep the parsing layer
    (:func:`_extract_transcript_text`) able to read text uniformly, this extractor
    downloads the best caption track's vtt text via yt-dlp's own urlopen and
    attaches it as ``data`` on that format — so the parser is agnostic to whether
    captions arrived inline (mock) or fetched (live).

    Args:
        video_url: The canonical watch URL.

    Returns:
        yt-dlp's ``extract_info`` dict, with the best English caption format's vtt
        text attached as ``data``.

    Raises:
        AdapterFetchError: When yt-dlp is not installed on the worker.
    """
    try:
        import yt_dlp  # noqa: PLC0415 — lazy import (optional heavy worker dep)
    except ImportError as exc:
        raise AdapterFetchError(
            message="yt-dlp is not installed",
            adapter_name=_ADAPTER_NAME,
            fix_suggestion="pip install yt-dlp (add to the worker image)",
        ) from exc

    with yt_dlp.YoutubeDL(_YTDLP_BASE_OPTS) as ydl:
        info = ydl.extract_info(video_url, download=False) or {}
        _attach_caption_text(ydl, info)
    return info


def _attach_caption_text(ydl: Any, info: dict[str, Any]) -> None:
    """Download the best English caption track's vtt text and attach it as ``data``.

    Mutates ``info`` in place: finds the first usable English (then any) caption
    format across ``subtitles`` / ``automatic_captions``, fetches its ``url`` via
    yt-dlp's urlopen (so cookies/headers match the extraction), and sets ``data``
    on that format. Failures are swallowed — a missing/failed caption fetch simply
    leaves no ``data``, which the parser reads as caption-less.

    Args:
        ydl: The active ``yt_dlp.YoutubeDL`` instance (used for urlopen).
        info: The ``extract_info`` dict to enrich in place.
    """
    for track_key in ("subtitles", "automatic_captions"):
        tracks = info.get(track_key) or {}
        if not isinstance(tracks, dict):
            continue
        for lang in _preferred_caption_langs(tracks):
            for fmt in tracks.get(lang) or []:
                if not isinstance(fmt, dict):
                    continue
                if fmt.get("ext") not in ("vtt", None) or not fmt.get("url"):
                    continue
                try:
                    fmt["data"] = (
                        ydl.urlopen(fmt["url"]).read().decode("utf-8", "replace")
                    )
                    return
                except Exception as exc:  # noqa: BLE001 — caption fetch is best-effort
                    logger.warning(
                        "youtube_caption_download_failed",
                        caption_url=str(fmt.get("url"))[:120],
                        error_type=type(exc).__name__,
                        fix_suggestion="Caption track fetch failed; trying the next track",
                    )
