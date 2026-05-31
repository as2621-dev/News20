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

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.models import DigestScript
from agents.pipeline.stages.forced_alignment import CaptionTrack
from agents.voice.gemini_tts import VOICE_MAP_GEMINI

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
) -> dict[str, Any]:
    """Build the ``stories`` insert payload (reference/supabase-schema.md).

    Args:
        story: The canonical story.
        story_id: The assigned ``stories.story_id`` (text PK).
        segment_slug: The ``story_segment_slug`` (a valid ``segment_slug`` enum).
        poster_url: Public poster URL (None if poster generation failed).
        coverage_counts: ``derive_coverage_counts`` output.
        blindspot_lean: ``derive_blindspot_lean`` output (nullable).

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


def build_detail_chunk_rows(story_id: str, body_text: str) -> list[dict[str, Any]]:
    """Build ``detail_chunks`` rows by paragraph-splitting the source body.

    The swipe-right Detail body is the readable article text chunked by
    paragraph (reference/supabase-schema.md § detail_chunks). We split the
    single-source body on blank lines; a body with no blank lines becomes one
    chunk.

    Args:
        story_id: The ``stories.story_id``.
        body_text: The canonical source body text.

    Returns:
        Ordered ``detail_chunks`` row payloads.
    """
    paragraphs = [para.strip() for para in body_text.split("\n\n") if para.strip()] or [
        body_text.strip()
    ]
    return [
        {"detail_story_id": story_id, "chunk_index": index, "chunk_text": paragraph}
        for index, paragraph in enumerate(paragraphs)
        if paragraph
    ]


def build_story_trust_row(
    story_id: str,
    coverage_counts: dict[str, int],
    blindspot_lean: str | None,
) -> dict[str, Any]:
    """Build the 1:1 ``story_trust`` row from the static coverage derivation."""
    return {
        "trust_story_id": story_id,
        "coverage_left_count": coverage_counts.get("left", 0),
        "coverage_center_count": coverage_counts.get("center", 0),
        "coverage_right_count": coverage_counts.get("right", 0),
        "coverage_outlet_count": coverage_counts.get("total", 0),
        "blindspot_lean": blindspot_lean,
        "opposing_view_text": None,
    }


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


def script_speaker_order(script: DigestScript) -> list[str]:
    """Return the ordered speaker labels for a digest script's turns.

    Used only to confirm both anchor voices are present (VOICE_MAP_GEMINI keys).
    """
    return [turn.speaker for turn in script.turns if turn.speaker in VOICE_MAP_GEMINI]
