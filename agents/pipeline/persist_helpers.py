"""Persist helpers: row-payload builders + static outlet→bias trust derivation.

Split out of ``agents/pipeline/persist.py`` to keep each file under 500 LoC
(CLAUDE.md file-size discipline). These are **pure** functions that turn the
in-memory pipeline models (``CanonicalStory``, ``DigestScript``,
``CaptionTrack``, ``StoryInterestTag``) into the exact column dicts the Supabase
tables expect (``reference/supabase-schema.md``). No I/O lives here — the
writer (``persist.py``) calls these to build payloads and then inserts/uploads.

TRUST DERIVATION — FLAGGED DEVIATION (Rule 12)
----------------------------------------------
The M2 trust layer (commit 0e76d50) is a **read-only** TypeScript feature
(``src/lib/detail/fetchStoryDetail.ts`` reads ``story_trust`` / ``story_sources``);
it ships NO importable Python trust-derivation module. Per the SP3 brief, persist
therefore derives ``story_trust`` / ``story_sources`` **minimally** from
``CanonicalStory.covering_outlets`` + a **static outlet→bias table** (the
AllSides/Ad Fontes one-time lookup mandated by ``reference/integrations.md`` §
"Trust / bias (NEW — static, not an API)" and master-plan Decision #6). The
blindspot rule (>70% of coverage on the other sides) follows
``reference/supabase-schema.md`` § ``story_trust``. This is intentionally a
first-pass static derivation, not a parallel scheme — when a richer M2 GKG/tone
trust source lands it should replace ``derive_story_trust`` here.
"""

from __future__ import annotations

from typing import Any

from agents.ingestion.dedup import normalize_url
from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.models import (
    CoverageReport,
    DetailKeyPoint,
    DetailTimelineEvent,
    DigestScript,
    KeyFigure,
    SecondAnalytic,
)
from agents.pipeline.stages.forced_alignment import CaptionTrack
from agents.voice.gemini_tts import VOICE_MAP_GEMINI

# Reason: the catch-all editorial segment when no matched interest resolves to a
# concrete one — mirrors persist.DEFAULT_SEGMENT_SLUG (segment_slug enum).
_DEFAULT_SEGMENT_SLUG = "wildcard"
_VALID_SEGMENT_SLUGS = frozenset(
    {"geopolitics", "markets", "tech", "sport", "wildcard"}
)

# Reason: the static AllSides/Ad Fontes outlet→bias lookup (reference/
# integrations.md: one-time static table, NOT a per-story API call). Keyed by
# the bare outlet domain GDELT reports (CanonicalStory.covering_outlets). Bias
# lean ∈ {'left','center','right'} matches the bias_lean enum. Unknown domains
# default to 'center' so coverage still counts (flagged below).
_OUTLET_BIAS_BY_DOMAIN: dict[str, str] = {
    # left
    "cnn.com": "left",
    "nytimes.com": "left",
    "washingtonpost.com": "left",
    "theguardian.com": "left",
    "msnbc.com": "left",
    "vox.com": "left",
    "politico.com": "left",
    "huffpost.com": "left",
    "nbcnews.com": "left",
    "abcnews.go.com": "left",
    # center
    "reuters.com": "center",
    "apnews.com": "center",
    "bbc.com": "center",
    "bbc.co.uk": "center",
    "bloomberg.com": "center",
    "axios.com": "center",
    "thehill.com": "center",
    "csmonitor.com": "center",
    "usatoday.com": "center",
    "espn.com": "center",
    "aljazeera.com": "center",
    # right
    "foxnews.com": "right",
    "wsj.com": "right",
    "nypost.com": "right",
    "washingtonexaminer.com": "right",
    "dailywire.com": "right",
    "breitbart.com": "right",
    "theepochtimes.com": "right",
    "nationalreview.com": "right",
    "telegraph.co.uk": "right",
    "dailymail.co.uk": "right",
}

# Reason: domains absent from the static table count toward coverage but their
# lean is unknown; default to 'center' so the outlet still appears and the
# breakdown sums to the outlet count. FLAGGED — a known-bias table miss biases
# the blindspot calc toward center; expand the table as outlets recur.
_DEFAULT_BIAS_LEAN = "center"

# Reason: blindspot fires when one side is materially under-covered — the schema
# rule is ">70% of coverage on the other sides" (reference/supabase-schema.md §
# story_trust). Operationally: the single MOST under-covered lean holds < 30% of
# total coverage (so the other two together hold > 70%) AND coverage is genuinely
# concentrated, i.e. one dominant lean holds a strict majority (> 50%). The
# majority guard stops a near-balanced 3-way split (where every minority is
# trivially < 30%) from falsely flagging a blindspot.
_BLINDSPOT_UNDERCOVERAGE_SHARE = 0.30
_BLINDSPOT_DOMINANT_SHARE = 0.50


def bias_lean_for_domain(outlet_domain: str) -> str:
    """Resolve an outlet domain to its static bias lean (defaults to center).

    Args:
        outlet_domain: The bare domain (e.g. ``"cnn.com"``).

    Returns:
        ``'left'`` / ``'center'`` / ``'right'`` — ``'center'`` when unknown.

    Example:
        >>> bias_lean_for_domain("foxnews.com")
        'right'
        >>> bias_lean_for_domain("unknown-blog.example")
        'center'
    """
    return _OUTLET_BIAS_BY_DOMAIN.get(outlet_domain.strip().lower(), _DEFAULT_BIAS_LEAN)


def _outlet_display_name(outlet_domain: str) -> str:
    """Derive a readable outlet name from a domain ("cnn.com" → "Cnn").

    Reason: ``story_sources.source_outlet_name`` is denormalized for display; we
    have only the domain at ingest, so title-case the registrable label. Real
    outlet names arrive when the static ``outlets`` table is joined (future).
    """
    label = outlet_domain.strip().lower()
    for suffix in (".com", ".co.uk", ".org", ".net", ".go.com"):
        if label.endswith(suffix):
            label = label[: -len(suffix)]
            break
    label = label.split(".")[-1] if "." in label else label
    return label.replace("-", " ").title() or outlet_domain


def derive_coverage_counts(covering_outlets: list[str]) -> dict[str, int]:
    """Bucket covering outlets into left/center/right coverage counts.

    Args:
        covering_outlets: Distinct outlet domains covering the story.

    Returns:
        ``{'left': int, 'center': int, 'right': int, 'total': int}``.

    Example:
        >>> derive_coverage_counts(["cnn.com", "foxnews.com", "reuters.com"])
        {'left': 1, 'center': 1, 'right': 1, 'total': 3}
    """
    counts = {"left": 0, "center": 0, "right": 0}
    for domain in covering_outlets:
        counts[bias_lean_for_domain(domain)] += 1
    counts["total"] = len(covering_outlets)
    return counts


def derive_blindspot_lean(coverage_counts: dict[str, int]) -> str | None:
    """Flag the under-covered lean per the >70%-on-other-sides blindspot rule.

    A side is a blindspot when the OTHER two sides together hold >70% of
    coverage AND this side holds <30%. With few outlets nothing fires (needs ≥4
    outlets for the share math to be meaningful), matching the schema note that
    blindspot is derived, not hardcoded.

    Args:
        coverage_counts: ``derive_coverage_counts`` output.

    Returns:
        The under-covered ``bias_lean`` (the blindspot), or ``None`` when
        coverage is balanced / too sparse to call.

    Example:
        >>> derive_blindspot_lean({'left': 8, 'center': 1, 'right': 0, 'total': 9})
        'right'
    """
    total = coverage_counts.get("total", 0)
    # Reason: too few outlets to call a blindspot responsibly.
    if total < 4:
        return None

    leans = ("left", "center", "right")
    shares = {lean: coverage_counts.get(lean, 0) / total for lean in leans}

    # Reason: a blindspot needs genuine concentration — one dominant lean with a
    # strict majority. A near-balanced split (no side > 50%) has no blindspot.
    dominant_share = max(shares.values())
    if dominant_share <= _BLINDSPOT_DOMINANT_SHARE:
        return None

    # The blindspot is the single MOST under-covered lean (the strict minimum),
    # provided it holds < 30% of coverage. Ties (two equally-minimal leans) do
    # not name a single blindspot.
    min_share = min(shares.values())
    if min_share >= _BLINDSPOT_UNDERCOVERAGE_SHARE:
        return None
    most_undercovered = [lean for lean, share in shares.items() if share == min_share]
    if len(most_undercovered) != 1:
        return None
    return most_undercovered[0]


def build_story_row(
    story: CanonicalStory,
    story_id: str,
    segment_slug: str,
    poster_url: str | None,
    coverage_counts: dict[str, int],
    blindspot_lean: str | None,
    key_figure: KeyFigure | None = None,
    detail_category: str | None = None,
    is_breaking: bool = False,
) -> dict[str, Any]:
    """Build the ``stories`` insert payload (reference/supabase-schema.md).

    Args:
        story: The canonical story.
        story_id: The assigned ``stories.story_id`` (text PK).
        segment_slug: The ``story_segment_slug`` (a valid ``segment_slug`` enum).
        poster_url: Public poster URL (None if poster generation failed).
        coverage_counts: ``derive_coverage_counts`` output.
        blindspot_lean: ``derive_blindspot_lean`` output (nullable).
        key_figure: The grounded hero ``KeyFigure`` (Phase 2c SP3 enrichment);
            ``None`` (or all-None fields) when the story has no key figure. Both
            ``stories.story_key_figure_*`` columns are nullable.
        detail_category: The resolved Detail-page category (one of the 9 buckets;
            ``detail_templates.detail_category_for`` output). Drives the Detail
            panel template. ``None`` only on legacy/unspecified callers (column is
            nullable; the UI null-guards).
        is_breaking: Whether the story is flagged breaking (from the GDELT coverage
            census). Persisted as ``stories.story_is_breaking``.

    Returns:
        A column dict for ``stories``.
    """
    published_iso = story.canonical_published_utc.isoformat()
    return {
        "story_id": story_id,
        "story_segment_slug": segment_slug,
        "story_headline": story.canonical_title,
        # Reason: dek is NOT NULL; fall back to the headline when no separate
        # subhead exists (single-source ingest carries only the title).
        "story_dek": story.canonical_title,
        "story_primary_outlet_name": story.canonical_primary_outlet_name
        or _outlet_display_name(story.canonical_primary_outlet_domain),
        "story_ambient_poster_url": poster_url,
        "story_first_reported_utc": published_iso,
        "story_last_updated_utc": published_iso,
        "story_outlet_count": story.story_outlet_count,
        "story_blindspot_lean": blindspot_lean,
        # Reason: the Detail hero key-figure card (Phase 2c). The value is already
        # source-grounded-or-None by SP3 enrichment — a fabricated number never
        # reaches this column (Decision #5).
        "story_key_figure_value": key_figure.key_figure_value if key_figure else None,
        "story_key_figure_label": key_figure.key_figure_label if key_figure else None,
        # Reason: the Detail page picks its per-category panel template off this key
        # (migration 0015); is_breaking lets the Breaking template win regardless of
        # the underlying topic.
        "story_detail_category": detail_category,
        "story_is_breaking": is_breaking,
    }


def build_digest_row(
    digest_story_id: str,
    audio_url: str,
    duration_ms: int,
    poster_url: str | None,
) -> dict[str, Any]:
    """Build the ``digests`` insert payload.

    ``digest_is_current`` is True so the partial unique index marks this as the
    one current digest per story (reference/supabase-schema.md).
    """
    return {
        "digest_story_id": digest_story_id,
        "digest_audio_url": audio_url,
        "digest_duration_ms": duration_ms,
        "digest_ambient_poster_url": poster_url,
        "digest_is_current": True,
    }


def build_caption_sentence_rows(
    digest_id: str,
    story_id: str,
    caption_track: CaptionTrack,
    turns_speaker_order: list[str],
) -> list[dict[str, Any]]:
    """Map a ``CaptionTrack`` to lossless ``caption_sentences`` row payloads.

    One row per sentence. ``word_tokens`` is the JSONB karaoke array in the
    schema shape ``{word_text, is_highlight, start_ms, end_ms}`` (the caption
    track carries seconds; we convert to ms). Each sentence carries exactly one
    highlight token (the alignment guarantees one ``is_highlight`` per
    sentence). ``anchor_speaker`` alternates ALEX/JORDAN by sentence index
    (``st.anchors[si % 2]`` in the prototype) — the locked anchor duo.

    Args:
        digest_id: The parent ``digests.digest_id``.
        story_id: The ``stories.story_id``.
        caption_track: The aligned caption track (forced_alignment output).
        turns_speaker_order: Unused placeholder kept for signature stability;
            sentence speaker is index-alternated per the prototype contract.

    Returns:
        Ordered ``caption_sentences`` row payloads (one per sentence).
    """
    # Reason: group the flat caption words by sentence_index, preserving order.
    sentences: dict[int, list[Any]] = {}
    for word in caption_track.words:
        sentences.setdefault(word.sentence_index, []).append(word)

    anchor_order = ["ALEX", "JORDAN"]
    rows: list[dict[str, Any]] = []
    for sentence_index in sorted(sentences.keys()):
        words = sentences[sentence_index]
        word_tokens = [
            {
                "word_text": word.word,
                "is_highlight": word.is_highlight,
                "start_ms": int(round(word.start_s * 1000)),
                "end_ms": int(round(word.end_s * 1000)),
            }
            for word in words
        ]
        highlight_word = next(
            (word.word for word in words if word.is_highlight), words[0].word
        )
        sentence_text = " ".join(word.word for word in words)
        rows.append(
            {
                "caption_digest_id": digest_id,
                "caption_story_id": story_id,
                "sentence_index": sentence_index,
                # Reason: anchors[si % 2] — locked prototype alternation.
                "anchor_speaker": anchor_order[sentence_index % 2],
                "sentence_text": sentence_text,
                "highlight_keyword": highlight_word,
                "sentence_start_ms": int(round(words[0].start_s * 1000)),
                "sentence_end_ms": int(round(words[-1].end_s * 1000)),
                "word_tokens": word_tokens,
            }
        )
    return rows


# Reason: a "long" body that "\n\n" failed to split is almost certainly a
# single-newline-separated article (trafilatura/candidate seeds emit those);
# short single-line bodies stay one chunk.
SINGLE_NEWLINE_FALLBACK_MIN_CHARS = 600


def _normalize_for_headline_compare(text: str) -> str:
    """Lowercase, collapse whitespace, and strip trailing punctuation for compare."""
    return " ".join(text.lower().split()).rstrip(".!?:;,")


def build_detail_chunk_rows(
    story_id: str, body_text: str, story_headline: str | None = None
) -> list[dict[str, Any]]:
    """Build ``detail_chunks`` rows by paragraph-splitting the source body.

    The swipe-right Detail body is the readable article text chunked by
    paragraph (reference/supabase-schema.md § detail_chunks). We split the
    single-source body on blank lines; when that yields a single paragraph for
    a long body we fall back to single-newline splitting (live source bodies
    separate paragraphs with one ``\\n``). A leading paragraph that duplicates
    the story headline is dropped (the Detail layer already renders the
    headline as its own ``.art-h1``).

    Args:
        story_id: The ``stories.story_id``.
        body_text: The canonical source body text.
        story_headline: Optional headline; a duplicate leading paragraph is
            stripped when it matches (normalized).

    Returns:
        Ordered ``detail_chunks`` row payloads (never empty for non-blank input).
    """
    paragraphs = [para.strip() for para in body_text.split("\n\n") if para.strip()]
    if len(paragraphs) <= 1 and len(body_text) >= SINGLE_NEWLINE_FALLBACK_MIN_CHARS:
        paragraphs = [para.strip() for para in body_text.split("\n") if para.strip()]
    if not paragraphs:
        paragraphs = [body_text.strip()]
    if (
        story_headline
        and len(paragraphs) > 1
        and _normalize_for_headline_compare(paragraphs[0])
        == _normalize_for_headline_compare(story_headline)
    ):
        paragraphs = paragraphs[1:]
    return [
        {"detail_story_id": story_id, "chunk_index": index, "chunk_text": paragraph}
        for index, paragraph in enumerate(paragraphs)
        if paragraph
    ]


def build_story_trust_row(
    story_id: str,
    coverage_counts: dict[str, int],
    blindspot_lean: str | None,
    coverage_report: CoverageReport | None = None,
) -> dict[str, Any]:
    """Build the 1:1 ``story_trust`` row (static counts + optional GDELT reach).

    When a Phase 2c ``coverage_report`` is supplied (the GDELT census, SP2), it is
    the authoritative source: its mode-correct counts + blindspot replace the
    static-derivation fallback, and the four reach columns
    (``coverage_mode`` / ``coverage_momentum`` / ``coverage_originating_outlet_name``
    / ``coverage_notable_outlet_names``) are populated. Without a report the row
    falls back to the legacy ``covering_outlets`` static derivation and the
    DB-default ``coverage_mode='partisan'`` (column omitted so the default holds).

    Args:
        story_id: The ``stories.story_id`` (FK).
        coverage_counts: ``derive_coverage_counts`` output (the static fallback).
        blindspot_lean: ``derive_blindspot_lean`` output (the static fallback).
        coverage_report: The GDELT ``CoverageReport`` (Phase 2c SP2), or None.

    Returns:
        A column dict for ``story_trust``.
    """
    if coverage_report is None:
        return {
            "trust_story_id": story_id,
            "coverage_left_count": coverage_counts.get("left", 0),
            "coverage_center_count": coverage_counts.get("center", 0),
            "coverage_right_count": coverage_counts.get("right", 0),
            "coverage_outlet_count": coverage_counts.get("total", 0),
            "blindspot_lean": blindspot_lean,
            "opposing_view_text": None,
        }
    return {
        "trust_story_id": story_id,
        "coverage_left_count": coverage_report.coverage_left_count,
        "coverage_center_count": coverage_report.coverage_center_count,
        "coverage_right_count": coverage_report.coverage_right_count,
        "coverage_outlet_count": coverage_report.coverage_outlet_count,
        "blindspot_lean": coverage_report.blindspot_lean,
        "opposing_view_text": None,
        # Reason: the Phase 2c adaptive-coverage reach columns (0004 ALTER).
        "coverage_mode": coverage_report.coverage_mode,
        "coverage_momentum": coverage_report.coverage_momentum,
        "coverage_originating_outlet_name": coverage_report.coverage_originating_outlet_name,
        "coverage_notable_outlet_names": coverage_report.coverage_notable_outlet_names,
    }


def build_story_timeline_rows(
    story_id: str, timeline: list[DetailTimelineEvent]
) -> list[dict[str, Any]]:
    """Build ordered ``story_timeline`` rows from the enrichment timeline.

    One row per "HOW IT DEVELOPED" event. ``timeline_event_index`` is taken
    verbatim from the (already contiguous, 0-based) enrichment events. A None
    ``timeline_event_at`` is omitted so the column's ``DEFAULT now()`` applies.

    Args:
        story_id: The ``stories.story_id`` (FK).
        timeline: The grounded, ordered ``DetailTimelineEvent``s (SP3).

    Returns:
        Ordered ``story_timeline`` row payloads (contiguous index order).
    """
    rows: list[dict[str, Any]] = []
    for event in timeline:
        row: dict[str, Any] = {
            "timeline_story_id": story_id,
            "timeline_event_index": event.timeline_event_index,
            "timeline_when_label": event.timeline_when_label,
            "timeline_what_text": event.timeline_what_text,
        }
        if event.timeline_event_at is not None:
            row["timeline_event_at"] = event.timeline_event_at
        rows.append(row)
    return rows


def build_story_analytics_rows(
    story_id: str, analytic_panels: list[SecondAnalytic]
) -> list[dict[str, Any]]:
    """Build the ordered ``story_analytics`` rows from a story's analytic panels.

    Was 1:1 (one row per story); now 1:N (migration 0013) — one row per Detail
    template ``analytic`` slot, each carrying its ``analytic_slot_index``. Each
    ``analytic_rows`` element is serialized from its ``AnalyticRow`` model to the
    exact JSONB shape Postgres stores — never a raw dict at the DB boundary
    (schema §0 types / Rule 9). ``analytic_story_id`` FKs the row to the story.

    Args:
        story_id: The ``stories.story_id`` (FK).
        analytic_panels: The grounded ``SecondAnalytic`` panels (1-3, slot-ordered).

    Returns:
        Ordered column dicts for ``story_analytics`` (one per panel).
    """
    return [
        {
            "analytic_story_id": story_id,
            "analytic_slot_index": panel.analytic_slot_index,
            "analytic_kind": panel.analytic_kind,
            "analytic_tab_label": panel.analytic_tab_label,
            "analytic_headline": panel.analytic_headline,
            "analytic_summary_text": panel.analytic_summary_text,
            # Reason: validate-then-dump each row so a malformed element can never
            # reach Postgres (the elements are AnalyticRow models; model_dump emits
            # the canonical JSONB shape with explicit nulls).
            "analytic_rows": [row.model_dump() for row in panel.analytic_rows],
            "analytic_is_grounded": panel.analytic_is_grounded,
        }
        for panel in analytic_panels
    ]


def build_detail_key_point_rows(
    story_id: str, key_points: list[DetailKeyPoint]
) -> list[dict[str, Any]]:
    """Build ordered ``detail_key_points`` rows from the 5 at-a-glance bullets.

    ``key_point_index`` is taken verbatim from the (0-based, contiguous)
    enrichment bullets — exactly 5 per story (SP3 enforces the count; this is a
    straight mapping).

    Args:
        story_id: The ``stories.story_id`` (FK).
        key_points: The 5 grounded ``DetailKeyPoint``s (SP3).

    Returns:
        Ordered ``detail_key_points`` row payloads.
    """
    return [
        {
            "key_point_story_id": story_id,
            "key_point_index": point.key_point_index,
            "key_point_text": point.key_point_text,
        }
        for point in key_points
    ]


def resolve_segment_from_tags(
    story_interest_tags: list[StoryInterestTag],
    interest_segment_lookup: dict[str, str] | None,
) -> str:
    """Resolve a story's ``story_segment_slug`` from its best-matched interest.

    The second-analytic kind + coverage mode are chosen deterministically from the
    segment (Decisions #2/#3), so the segment must reflect the interest the story
    most-closely serves. We pick the lowest ``story_interest_match_depth`` tag (the
    leaf / closest match) whose interest resolves to a valid segment in the
    injected ``interest_segment_lookup`` (``{interest_id: segment_slug}`` built once
    per batch from the ``interests`` table, where depth-0 rows carry the segment and
    leaves inherit their root's). Falls back to ``wildcard`` when nothing resolves.

    Args:
        story_interest_tags: The story's ``story_interests`` tags (interest_id +
            relative match depth).
        interest_segment_lookup: ``{interest_id: segment_slug}`` (injected; None or
            empty → wildcard).

    Returns:
        A valid ``segment_slug`` enum value (``wildcard`` when unresolved).

    Example:
        >>> tags = [StoryInterestTag(story_interest_story_id="s1",
        ...     story_interest_interest_id="int-world", story_interest_match_depth=0)]
        >>> resolve_segment_from_tags(tags, {"int-world": "geopolitics"})
        'geopolitics'
        >>> resolve_segment_from_tags(tags, {})
        'wildcard'
    """
    if not interest_segment_lookup:
        return _DEFAULT_SEGMENT_SLUG
    # Reason: closest match first — a leaf (depth 0) is more specific than an
    # ancestor (depth 1/2), so it best characterizes the story's segment.
    for tag in sorted(story_interest_tags, key=lambda t: t.story_interest_match_depth):
        segment = interest_segment_lookup.get(tag.story_interest_interest_id)
        if segment in _VALID_SEGMENT_SLUGS:
            return segment
    return _DEFAULT_SEGMENT_SLUG


def build_story_source_rows(
    story_id: str, story: CanonicalStory
) -> list[dict[str, Any]]:
    """Build ``story_sources`` rows from the story's covering outlets.

    One row per covering outlet, with its static bias lean. The representative
    outlet carries the canonical article URL; others carry the domain only.

    Args:
        story_id: The ``stories.story_id``.
        story: The canonical story (carries ``covering_outlets`` + primary URL).

    Returns:
        ``story_sources`` row payloads (deduped by outlet name via the schema's
        unique constraint — we dedupe here too).
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for domain in story.covering_outlets or [story.canonical_primary_outlet_domain]:
        outlet_name = _outlet_display_name(domain)
        if outlet_name in seen:
            continue
        seen.add(outlet_name)
        is_primary = domain == story.canonical_primary_outlet_domain
        rows.append(
            {
                "source_story_id": story_id,
                "source_outlet_name": outlet_name,
                "source_bias_lean": bias_lean_for_domain(domain),
                "source_article_url": story.canonical_url if is_primary else None,
                "source_published_utc": story.canonical_published_utc.isoformat(),
                "source_is_citation": True,
            }
        )
    return rows


def build_story_interest_rows(
    story_id: str, story_interest_tags: list[StoryInterestTag]
) -> list[dict[str, Any]]:
    """Build ``story_interests`` rows from the SP1 tag payloads.

    Args:
        story_id: The ``stories.story_id`` (overrides the provisional id on the
            tags so all rows FK to the persisted story).
        story_interest_tags: The story's ``story_interests`` tag payloads.

    Returns:
        ``story_interests`` row payloads (deduped by interest id).
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tag in story_interest_tags:
        if tag.story_interest_interest_id in seen:
            continue
        seen.add(tag.story_interest_interest_id)
        rows.append(
            {
                "story_interest_story_id": story_id,
                "story_interest_interest_id": tag.story_interest_interest_id,
                "story_interest_match_depth": tag.story_interest_match_depth,
                "story_interest_relevance": tag.story_interest_relevance,
            }
        )
    return rows


def build_story_url_alias_rows(
    story_id: str, story: CanonicalStory
) -> list[dict[str, Any]]:
    """Build ``story_url_aliases`` rows — every covering-outlet URL → this story id.

    The cross-day produce-once aid (migration 0006): persisting these means a
    later batch that re-clusters the SAME event (different earliest member, so a
    different freshly-derived ``canonical_story_id``) can normalize its members'
    URLs, find one already aliased here, and REUSE this ``story_id`` — keeping one
    id per event across days so produce-once + don't-repeat hold.

    URLs are normalized with the SAME ``normalize_url`` the clusterer + resolver
    use, so write-time and lookup-time keys match exactly. Empty/blank URLs are
    dropped; the set is deduped and sorted for deterministic output.

    Args:
        story_id: The persisted ``stories.story_id`` to alias every URL to.
        story: The canonical story (carries ``member_candidate_ids`` = the member
            article URLs, plus the representative ``canonical_url`` /
            ``canonical_normalized_url``).

    Returns:
        ``story_url_aliases`` row payloads (one per distinct normalized URL).
    """
    urls = {normalize_url(member_url) for member_url in story.member_candidate_ids}
    urls.add(normalize_url(story.canonical_url))
    if story.canonical_normalized_url:
        urls.add(story.canonical_normalized_url)
    urls.discard("")
    return [
        {"alias_normalized_url": url, "alias_story_id": story_id}
        for url in sorted(urls)
    ]


def build_suggested_question_rows(
    story_id: str, questions: list[str]
) -> list[dict[str, Any]]:
    """Build ``suggested_questions`` rows from a list of question strings."""
    return [
        {
            "question_story_id": story_id,
            "question_index": index,
            "question_text": question,
        }
        for index, question in enumerate(questions)
        if question.strip()
    ]


def load_outlets_lookup(supabase_client: Any) -> dict[str, str]:
    """Load the ``{outlet_domain: outlet_bias_lean}`` map from the ``outlets`` table.

    Read ONCE per batch (the GDELT coverage census resolves every domain against
    this dict, SP2). Only rows with a non-null ``outlet_domain`` AND a non-null
    ``outlet_bias_lean`` are usable (a domain with no lean can't rate coverage).
    The client is INJECTED (service-role read) — tests pass a mock; this never
    reads a secret itself.

    Args:
        supabase_client: A service-role supabase client (injected; mocked in tests).

    Returns:
        ``{outlet_domain: bias_lean}`` for every rated, domain-bearing outlet row.
    """
    response = (
        supabase_client.table("outlets")
        .select("outlet_domain,outlet_bias_lean")
        .execute()
    )
    rows = getattr(response, "data", None) or []
    lookup: dict[str, str] = {}
    for row in rows:
        domain = row.get("outlet_domain")
        lean = row.get("outlet_bias_lean")
        if domain and lean:
            lookup[str(domain).strip().lower()] = str(lean)
    return lookup


def script_speaker_order(script: DigestScript) -> list[str]:
    """Return the ordered speaker labels for a digest script's turns.

    Used only to confirm both anchor voices are present (VOICE_MAP_GEMINI keys).
    """
    return [turn.speaker for turn in script.turns if turn.speaker in VOICE_MAP_GEMINI]
