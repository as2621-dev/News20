"""Stage: LLM detail-enrichment — the grounded richer Detail payload (Phase 2c SP3).

Produces the richer Story Detail payload the M2 design calls for, constrained to
the **single source** (Decision #4) and **verification-gated** for numbers
(Decision #5 / Rule 12): a hero ``KeyFigure``, ordered ``DetailTimelineEvent``s
("HOW IT DEVELOPED"), the segment-skinned ``SecondAnalytic``, and exactly 5
``DetailKeyPoint``s.

The LLM drafts the narrative; **the grounding of every numeric value is decided
in code, not by the model** (Rule 5). A market number that the source body does
not contain is dropped to direction-only and ``analytic_is_grounded`` is set
False — a fabricated figure must NEVER publish as a fact. The same gate applies
to the hero key figure.

The segment → ``analytic_kind`` selection is a PURE function
(:func:`select_analytic_kind`) — deterministic in code, never the LLM
(Decision #2 / Rule 5).

``DetailEnrichment`` is defined LOCALLY here: SP1 shipped the leaf models
(``KeyFigure`` / ``DetailTimelineEvent`` / ``SecondAnalytic`` / ``DetailKeyPoint``)
in ``agents/pipeline/models.py`` but no aggregate, and this sub-phase must not
edit ``models.py``. SP4 imports this aggregate to persist the payload.

The Gemini call is mocked at the ``LLMClient`` boundary in every test — no live
call, no cost (CLAUDE.md mocking mandate).

Input:  a ``CanonicalStory`` (SP1) + its ``DigestScript`` (SP2) + an ``LLMClient``
        + the resolved ``segment_slug`` (SP4 resolves it; defaults to wildcard)
Output: a grounded :class:`DetailEnrichment`

Example:
    >>> from agents.pipeline.stages.detail_enrichment import run_detail_enrichment
    >>> enrichment = await run_detail_enrichment(  # doctest: +SKIP
    ...     story=canonical_story, script=digest_script,
    ...     llm_client=client, segment_slug="geopolitics",
    ... )
    >>> enrichment.second_analytic.analytic_kind
    'subject_profile'
"""

from __future__ import annotations

import re
import time
from typing import Any

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory
from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.models import (
    AnalyticKind,
    AnalyticRow,
    DetailKeyPoint,
    DetailTimelineEvent,
    DigestScript,
    KeyFigure,
    SecondAnalytic,
)
from agents.pipeline.prompts import (
    DETAIL_ANALYTIC_INSTRUCTIONS,
    DETAIL_ENRICHMENT_PROMPT,
)
from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.detail_enrichment")

# Reason: low temperature — enrichment is extraction/structuring, not creative
# writing; matches the verification stage's stable-classification temperature.
DETAIL_ENRICHMENT_TEMPERATURE = 0.2

# Reason: cap the source body fed to the model (mirrors scripting's cap) so a long
# article doesn't blow the context budget; the lede carries the figures.
_MAX_SOURCE_BODY_CHARS = 8000

# Exactly 5 at-a-glance bullets (Decision #6). If the model returns more we take the
# first 5 (most-important-first); fewer is a hard failure (fail loud, Rule 12).
_REQUIRED_KEY_POINTS = 5

# Reason: the deterministic segment → analytic_kind map (2026-06-12 product
# decision, supersedes Decision #2's original table): MARKET IMPACT exists ONLY
# for markets + tech stories; every other segment gets the PROFILE of the
# story's central person/organization. Any slug not in this map (incl. future/
# unknown values) falls back to why_it_matters — the always-safe kind that
# needs no domain figures.
_SEGMENT_TO_ANALYTIC_KIND: dict[str, AnalyticKind] = {
    "geopolitics": "subject_profile",
    "markets": "market_impact",
    "tech": "market_impact",
    "sport": "subject_profile",
    "wildcard": "subject_profile",
}

# The human tab label per analytic_kind (Decision #2 table). Fixed by the kind, not
# drafted by the model — keeps the UI label trustworthy.
_ANALYTIC_TAB_LABELS: dict[AnalyticKind, str] = {
    "market_impact": "MARKET IMPACT",
    "ripple": "RIPPLE",
    "impact": "IMPACT",
    "stakes": "STAKES",
    "why_it_matters": "WHY IT MATTERS",
    "subject_profile": "PROFILE",
}

# Reason: subject_profile rows explicitly noted as background (well-known facts
# about famous public figures, the ONE sanctioned exception to the single-source
# rule) are exempt from the numeric source-grounding drop — a birth year or
# papacy start date will legitimately not appear in the day's article.
_BACKGROUND_NOTE_VALUE = "background"

# Reason: a "value carries a number" test. Any run of 2+ digits, or a single digit
# next to a unit/symbol ($ % bn etc.), means the value asserts a figure that MUST be
# source-grounded. Pure-text values ("record high", "all-time peak") carry no number
# and need no numeric grounding.
_DIGIT_RUN_REGEX = re.compile(r"\d")

# Reason: digits-and-grouping only, for the source-membership test. We strip
# everything but digits, dot and comma from a value, then check each maximal numeric
# token appears in the source body's digit stream — robust to "+4%" vs "4 percent",
# "$81.6B" vs "81.6 billion", and to thousands separators.
_NUMERIC_TOKEN_REGEX = re.compile(r"\d[\d.,]*")


class DetailEnrichment(BaseModel):
    """The complete grounded Detail enrichment for one story (LOCAL aggregate).

    Defined here (not in ``models.py``) because SP1 shipped only the leaf models
    and this sub-phase must not edit ``models.py``. SP4 imports this to persist:
    ``key_figure`` → ``stories.story_key_figure_*``, ``timeline`` →
    ``story_timeline`` rows, ``second_analytic`` → the ``story_analytics`` row,
    ``key_points`` → ``detail_key_points`` rows.

    Every numeric value carried here has already passed the source-grounding gate:
    an unsupported number was dropped to direction-only and
    ``second_analytic.analytic_is_grounded`` reflects the verdict (Decision #5).

    Attributes:
        enrichment_story_id: The story this enrichment is for (FK stories.story_id).
        key_figure: The hero key-figure card (both fields may be None).
        timeline: Ordered "HOW IT DEVELOPED" events (by ``timeline_event_index``).
        second_analytic: The segment-skinned second-analytic tab.
        key_points: Exactly 5 at-a-glance bullets (ordered by index).

    Example:
        >>> enrichment = DetailEnrichment(
        ...     enrichment_story_id="s1",
        ...     key_figure=KeyFigure(),
        ...     timeline=[],
        ...     second_analytic=SecondAnalytic(
        ...         analytic_story_id="s1", analytic_kind="why_it_matters",
        ...         analytic_tab_label="WHY IT MATTERS", analytic_headline="h",
        ...         analytic_summary_text="s",
        ...     ),
        ...     key_points=[DetailKeyPoint(key_point_index=i, key_point_text="x") for i in range(5)],
        ... )
        >>> len(enrichment.key_points)
        5
    """

    enrichment_story_id: str = Field(
        ..., description="Story this enrichment is for (FK stories.story_id)"
    )
    key_figure: KeyFigure = Field(
        ..., description="Hero key-figure card (both fields may be None)"
    )
    timeline: list[DetailTimelineEvent] = Field(
        default_factory=list,
        description="Ordered 'HOW IT DEVELOPED' events (by timeline_event_index)",
    )
    second_analytic: SecondAnalytic = Field(
        ..., description="The segment-skinned second-analytic tab"
    )
    key_points: list[DetailKeyPoint] = Field(
        ..., description="Exactly 5 at-a-glance bullets (ordered by index)"
    )


def select_analytic_kind(segment_slug: str) -> AnalyticKind:
    """Map a story's ``segment_slug`` to its ``analytic_kind`` (PURE, deterministic).

    The second-analytic kind is chosen by code from the segment, NEVER by the LLM
    (Decision #2 / Rule 5). MARKET IMPACT exists only for markets + tech; the
    other segments get the subject PROFILE. Unknown segments fall back to
    ``why_it_matters`` — the always-safe kind that asserts no domain figure.

    Args:
        segment_slug: The story's ``story_segment_slug`` ('geopolitics', 'markets',
            'tech', 'sport', 'wildcard', or any other value).

    Returns:
        The deterministic ``AnalyticKind`` for that segment.

    Example:
        >>> select_analytic_kind("markets")
        'market_impact'
        >>> select_analytic_kind("geopolitics")
        'subject_profile'
        >>> select_analytic_kind("something-unknown")
        'why_it_matters'
    """
    return _SEGMENT_TO_ANALYTIC_KIND.get(segment_slug, "why_it_matters")


def _source_digit_stream(source_body: str) -> str:
    """Reduce the source body to its bare digit stream for numeric membership tests.

    Drops every non-digit so "$81.6 billion" and "rose 4%" both contribute their
    digit runs ("816", "4") to one searchable string. Thousands separators and
    decimal points are dropped too, so "1,234" in a value matches "1234" in the
    body and vice-versa.

    Args:
        source_body: The single source article body text.

    Returns:
        The concatenated digits of the source body.
    """
    return re.sub(r"\D", "", source_body)


def _value_has_number(value: str | None) -> bool:
    """Whether a row/figure value asserts a numeric figure (vs pure text).

    Args:
        value: The candidate value string, or None.

    Returns:
        True if the value contains any digit.
    """
    return bool(value) and bool(_DIGIT_RUN_REGEX.search(value or ""))


def _is_number_grounded(value: str, source_digits: str) -> bool:
    """Whether every numeric token in *value* appears in the source's digit stream.

    Trust-critical (Decision #5 / Rule 12): the grounding verdict is computed in
    code, not trusted from the model. A value like "+4%" grounds iff "4" appears in
    the source digit stream; "$81.6B" grounds iff "816" appears (separators
    stripped). If ANY numeric token in the value is absent from the source, the
    value is NOT grounded and the caller must drop it.

    Args:
        value: A value string known to contain at least one digit.
        source_digits: The source body reduced to its digit stream
            (:func:`_source_digit_stream`).

    Returns:
        True only when every numeric token in *value* is found in *source_digits*.
    """
    tokens = _NUMERIC_TOKEN_REGEX.findall(value)
    if not tokens:
        return False
    for token in tokens:
        token_digits = re.sub(r"\D", "", token)
        if not token_digits or token_digits not in source_digits:
            return False
    return True


def _ground_analytic_rows(
    raw_rows: list[Any],
    source_digits: str,
    story_id: str,
    analytic_kind: AnalyticKind,
) -> tuple[list[AnalyticRow], bool]:
    """Validate + source-ground each drafted analytic row's numeric value.

    For each row: validate the label, keep the direction/note, and gate the value.
    A value with no digits is kept as-is (pure-text, no number to ground). A value
    that carries a number is kept ONLY if every numeric token is found in the
    source digit stream; otherwise the value is DROPPED to None (direction-only)
    and the analytic is marked ungrounded.

    EXCEPTION (the one sanctioned hole in the single-source rule): a
    ``subject_profile`` row whose note is ``"background"`` may carry numbers not
    in the article (a birth year, a career figure) — these are well-known facts
    about famous public figures the prompt allows from general knowledge, and
    they are kept verbatim without affecting ``analytic_is_grounded``.

    Args:
        raw_rows: The model's drafted row objects (untrusted dicts).
        source_digits: The source body's digit stream.
        story_id: The story id (for logging the dropped-number event).
        analytic_kind: The code-chosen kind (gates the background exemption).

    Returns:
        ``(validated_rows, all_numbers_grounded)`` — the second element is False if
        ANY numeric value was dropped.
    """
    rows: list[AnalyticRow] = []
    all_grounded = True
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        label = str(raw_row.get("analytic_row_label", "")).strip()
        if not label:
            continue
        value = _clean_optional_str(raw_row.get("analytic_row_value"))
        direction = _clean_direction(raw_row.get("analytic_row_direction"))
        note = _clean_optional_str(raw_row.get("analytic_row_note"))

        is_background_profile_row = (
            analytic_kind == "subject_profile"
            and note is not None
            and note.lower() == _BACKGROUND_NOTE_VALUE
        )
        if (
            not is_background_profile_row
            and _value_has_number(value)
            and not _is_number_grounded(value, source_digits)
        ):
            # Reason: trust gate — an ungrounded number must NEVER publish as a fact.
            # Drop it to direction-only and flag the whole analytic ungrounded.
            logger.warning(
                "detail_analytic_number_dropped",
                story_id=story_id,
                row_label=label,
                dropped_value=value,
                fix_suggestion="Drafted figure not found in the source body; rendered "
                "direction-only. analytic_is_grounded set False.",
            )
            value = None
            all_grounded = False

        rows.append(
            AnalyticRow(
                analytic_row_label=label,
                analytic_row_value=value,
                analytic_row_direction=direction,
                analytic_row_note=note,
            )
        )
    return rows, all_grounded


def _clean_optional_str(raw: Any) -> str | None:
    """Coerce a raw model field to a non-empty stripped string, or None.

    Treats the literal strings ``"null"`` / ``"none"`` (which models sometimes emit
    inside JSON) as None so they never render as a fake value.

    Args:
        raw: The raw value from the parsed model JSON.

    Returns:
        The cleaned string, or None when empty / null-like.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    return text


def _clean_direction(raw: Any) -> str | None:
    """Coerce a raw direction to a valid glyph ('up'|'down'|'flat'), else None.

    Args:
        raw: The raw direction value from the parsed model JSON.

    Returns:
        A valid direction literal, or None.
    """
    text = _clean_optional_str(raw)
    if text is None:
        return None
    lowered = text.lower()
    return lowered if lowered in {"up", "down", "flat"} else None


def _ground_key_figure(raw_figure: Any, source_digits: str, story_id: str) -> KeyFigure:
    """Validate + source-ground the hero key figure (drop an ungrounded number).

    A key figure that carries a number must have that number in the source; an
    ungrounded figure value is dropped (the card hides rather than publishing a
    fabricated headline number, Decision #5).

    Args:
        raw_figure: The model's drafted key_figure object (untrusted).
        source_digits: The source body's digit stream.
        story_id: The story id (for logging).

    Returns:
        A validated :class:`KeyFigure` (value None if ungrounded or absent).
    """
    if not isinstance(raw_figure, dict):
        return KeyFigure()
    value = _clean_optional_str(raw_figure.get("key_figure_value"))
    label = _clean_optional_str(raw_figure.get("key_figure_label"))
    if _value_has_number(value) and not _is_number_grounded(value, source_digits):
        logger.warning(
            "detail_key_figure_number_dropped",
            story_id=story_id,
            dropped_value=value,
            fix_suggestion="Hero key figure not found in the source body; dropped.",
        )
        value = None
    return KeyFigure(key_figure_value=value, key_figure_label=label)


def _build_timeline(raw_timeline: Any) -> list[DetailTimelineEvent]:
    """Validate the drafted timeline into ordered ``DetailTimelineEvent``s.

    The model emits beats earliest-first; the 0-based ``timeline_event_index`` is
    assigned here by position (the model is not trusted to number them). Beats with
    an empty when-label or what-text are skipped.

    Args:
        raw_timeline: The model's drafted timeline array (untrusted).

    Returns:
        Ordered timeline events with contiguous 0-based indices.
    """
    events: list[DetailTimelineEvent] = []
    if not isinstance(raw_timeline, list):
        return events
    for raw_event in raw_timeline:
        if not isinstance(raw_event, dict):
            continue
        when_label = str(raw_event.get("timeline_when_label", "")).strip()
        what_text = str(raw_event.get("timeline_what_text", "")).strip()
        if not when_label or not what_text:
            continue
        events.append(
            DetailTimelineEvent(
                timeline_event_index=len(events),
                timeline_when_label=when_label,
                timeline_what_text=what_text,
                timeline_event_at=_clean_optional_str(
                    raw_event.get("timeline_event_at")
                ),
            )
        )
    return events


def _build_key_points(raw_points: Any, story_id: str) -> list[DetailKeyPoint]:
    """Validate the drafted key points into exactly 5 ordered ``DetailKeyPoint``s.

    Takes the first 5 non-empty bullets (most-important-first). Fewer than 5 is a
    hard failure — the Detail design requires exactly 5 (fail loud, Rule 12), so we
    never silently pad with blanks.

    Args:
        raw_points: The model's drafted key_points array (untrusted).
        story_id: The story id (for the error message).

    Returns:
        Exactly 5 ordered :class:`DetailKeyPoint`s.

    Raises:
        PipelineStageError: If fewer than 5 non-empty bullets were produced.
    """
    texts: list[str] = []
    if isinstance(raw_points, list):
        for raw_point in raw_points:
            text = str(raw_point).strip()
            if text:
                texts.append(text)
    if len(texts) < _REQUIRED_KEY_POINTS:
        raise PipelineStageError(
            stage="detail_enrichment",
            message=f"Detail enrichment produced {len(texts)} key points; "
            f"exactly {_REQUIRED_KEY_POINTS} are required",
            fix_suggestion="Re-run enrichment; the model must emit 5 at-a-glance bullets.",
        )
    return [
        DetailKeyPoint(key_point_index=index, key_point_text=text)
        for index, text in enumerate(texts[:_REQUIRED_KEY_POINTS])
    ]


def _build_system_prompt(story: CanonicalStory, analytic_kind: AnalyticKind) -> str:
    """Fill the detail-enrichment prompt with this story's source + analytic kind.

    Args:
        story: The canonical story whose body/headline/outlet seed the prompt.
        analytic_kind: The code-chosen second-analytic kind (drives the instruction).

    Returns:
        The system prompt with every ``{PLACEHOLDER}`` substituted.
    """
    body = (story.canonical_body_text or "").strip()
    if len(body) > _MAX_SOURCE_BODY_CHARS:
        body = body[:_MAX_SOURCE_BODY_CHARS]
    published = story.canonical_published_utc.strftime("%B %d, %Y")
    outlet = (
        story.canonical_primary_outlet_name or story.canonical_primary_outlet_domain
    )
    instruction = DETAIL_ANALYTIC_INSTRUCTIONS[analytic_kind]
    return (
        DETAIL_ENRICHMENT_PROMPT.replace("{ANALYTIC_INSTRUCTION}", instruction)
        .replace("{SOURCE_HEADLINE}", story.canonical_title)
        .replace("{SOURCE_OUTLET}", outlet)
        .replace("{SOURCE_PUBLISHED}", published)
        .replace("{SOURCE_BODY}", body)
    )


async def run_detail_enrichment(
    story: CanonicalStory,
    script: DigestScript,
    llm_client: LLMClient,
    *,
    segment_slug: str = "wildcard",
) -> DetailEnrichment:
    """Produce the grounded Detail enrichment for one canonical story.

    Single Gemini call. The story's ``canonical_body_text`` is the ONLY source of
    facts; the second-analytic kind is chosen in code from ``segment_slug``
    (Decision #2). Every numeric value the model drafts is then gated against the
    source body in code (Decision #5 / Rule 12): an unsupported number is dropped
    to direction-only and ``analytic_is_grounded`` is set False — a fabricated
    figure NEVER publishes as a fact.

    Args:
        story: The canonical story to enrich. Must carry ``canonical_body_text``.
        script: The digest script (carried for provenance / the story id; the
            grounding corpus is the story body, not the script).
        llm_client: An initialized ``LLMClient`` (mocked in tests).
        segment_slug: The resolved ``story_segment_slug`` (SP4 resolves it). Drives
            the deterministic second-analytic kind; defaults to ``wildcard``.

    Returns:
        A grounded :class:`DetailEnrichment` (5 key points, ordered timeline, a
        segment-correct ``SecondAnalytic``, a grounded hero key figure).

    Raises:
        PipelineStageError: If the story has no body text, the model returns a
            non-object response, or fewer than 5 key points are produced.

    Example:
        >>> enrichment = await run_detail_enrichment(  # doctest: +SKIP
        ...     story=story, script=script, llm_client=client, segment_slug="markets",
        ... )
        >>> enrichment.second_analytic.analytic_kind
        'market_impact'
    """
    source_body = (story.canonical_body_text or "").strip()
    if not source_body:
        raise PipelineStageError(
            stage="detail_enrichment",
            message="Canonical story has no body text to enrich + ground against",
            fix_suggestion="Ensure SP1 extracted canonical_body_text before enrichment",
        )

    analytic_kind = select_analytic_kind(segment_slug)
    start_time = time.monotonic()
    logger.info(
        "detail_enrichment_started",
        story_id=story.canonical_story_id,
        segment_slug=segment_slug,
        analytic_kind=analytic_kind,
        source_chars=len(source_body),
    )

    system_prompt = _build_system_prompt(story, analytic_kind)
    user_prompt = (
        "Produce the Detail enrichment now. Use ONLY the SOURCE_ARTICLE; copy every "
        "number verbatim or omit it. Output ONLY the JSON object."
    )
    raw_response = await llm_client.call_gemini(
        prompt=user_prompt,
        system=system_prompt,
        temperature=DETAIL_ENRICHMENT_TEMPERATURE,
    )

    parsed = extract_json_from_llm_response(raw_response, stage="detail_enrichment")
    if not isinstance(parsed, dict):
        raise PipelineStageError(
            stage="detail_enrichment",
            message="Detail enrichment LLM response is not a JSON object",
            fix_suggestion="Model returned non-object output — tighten the enrichment prompt.",
        )

    source_digits = _source_digit_stream(source_body)
    story_id = story.canonical_story_id

    key_figure = _ground_key_figure(parsed.get("key_figure"), source_digits, story_id)
    timeline = _build_timeline(parsed.get("timeline"))
    key_points = _build_key_points(parsed.get("key_points"), story_id)

    raw_analytic = parsed.get("second_analytic")
    if not isinstance(raw_analytic, dict):
        raw_analytic = {}
    rows, rows_grounded = _ground_analytic_rows(
        raw_analytic.get("analytic_rows", []) or [],
        source_digits,
        story_id,
        analytic_kind,
    )
    second_analytic = SecondAnalytic(
        analytic_story_id=story_id,
        analytic_kind=analytic_kind,
        analytic_tab_label=_ANALYTIC_TAB_LABELS[analytic_kind],
        analytic_headline=str(raw_analytic.get("analytic_headline", "")).strip()
        or _ANALYTIC_TAB_LABELS[analytic_kind].title(),
        analytic_summary_text=str(raw_analytic.get("analytic_summary_text", "")).strip()
        or "See the source article for detail.",
        analytic_rows=rows,
        # Reason: grounded only when EVERY numeric row value survived the source gate.
        analytic_is_grounded=rows_grounded,
    )

    enrichment = DetailEnrichment(
        enrichment_story_id=story_id,
        key_figure=key_figure,
        timeline=timeline,
        second_analytic=second_analytic,
        key_points=key_points,
    )

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "detail_enrichment_completed",
        story_id=story_id,
        analytic_kind=analytic_kind,
        timeline_event_count=len(timeline),
        analytic_row_count=len(rows),
        analytic_is_grounded=second_analytic.analytic_is_grounded,
        key_figure_present=key_figure.key_figure_value is not None,
        elapsed_ms=elapsed_ms,
    )
    return enrichment
