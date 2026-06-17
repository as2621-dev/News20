"""Pre-generation near-duplicate dedup of the produce shortlist (LLM judge).

WHY THIS STAGE EXISTS
---------------------
Ingestion clustering (:mod:`agents.ingestion.dedup`) merges stories only by an
identical normalized URL or a title similarity ``>= 0.85`` (character-level
``SequenceMatcher``). Two outlets covering the SAME event with differently-worded
headlines slip through as two separate ``CanonicalStory`` objects — e.g. the
2026-06-16 pair "Nvidia CEO Jensen Huang Urges Society to Adapt to AI" vs
"Nvidia CEO Jensen Huang urges societal change for AI era" (similarity 0.759,
below the bar). Both then pass the produce gate independently and the pipeline
spends the full per-story generation cost (script LLM → verify LLM → TTS → poster
→ editorial rewrite) on each — duplicated effort for one real story.

Lexical similarity is the WRONG signal here: it both MISSES that pair (0.759) and
would FALSELY merge genuinely distinct stories ("Apple reports Q1 earnings" vs
"…Q3 earnings" = 0.96). "Same event / same narrow angle" is a semantic judgment,
so this stage uses the LLM as the judge (CLAUDE.md Rule 5 — model for judgment,
code for the deterministic transform). The LLM only decides WHICH stories are
duplicates; the code deterministically picks which one to KEEP (highest coverage)
and drops the rest, BEFORE the expensive fan-out.

PLACEMENT
---------
Runs right after the produce gate and BEFORE the per-category caps
(``agents/pipeline/daily_batch.py``), so the caps fill from a de-duplicated pool
and can backfill freed capacity with genuinely different stories.

FAIL-OPEN
---------
A dedup miss is a cost/quality nicety, not a correctness gate — it must never
crash or block the daily run. Any LLM/parse error is logged loudly (Rule 12) and
the original shortlist is returned unchanged.

The Gemini SDK is mocked at the ``llm_client`` boundary in tests — no live call,
no cost (CLAUDE.md mocking mandate).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from agents.ingestion.models import CanonicalStory
from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.shared.logger import get_logger

logger = get_logger("pipeline.produce_dedup")

# Reason: a single LLM judge over today's whole produce shortlist (tens of
# stories) is cheap relative to the generation it prevents; pin the same proven
# text model the other SP2 stages use.
DEDUP_JUDGE_MODEL = "gemini-2.5-flash"

# Reason: low temperature — this is a near-deterministic clustering judgment, not
# creative writing.
DEDUP_JUDGE_TEMPERATURE = 0.1

# Reason: the lead snippet handed to the judge per story; enough to disambiguate
# the angle without bloating the prompt.
_LEAD_SNIPPET_CHARS = 220

DEDUP_JUDGE_SYSTEM = (
    "You are a news desk editor de-duplicating a list of candidate stories before "
    "the newsroom spends effort producing each one. Group together any stories that "
    "are REDUNDANT to produce side by side: they cover the SAME underlying event, OR "
    "the same narrow angle / sub-topic such that a reader would see them as the same "
    "story told twice. Do NOT group stories that merely share a broad topic, company, "
    "or person but report DIFFERENT events, figures, or angles (e.g. a Q1 earnings "
    "story and a Q3 earnings story about the same company are DISTINCT; two different "
    "product launches by the same company are DISTINCT). When unsure, keep them "
    "SEPARATE. Return ONLY a JSON array of duplicate groups; each group is a JSON "
    'array of the integer "n" values of stories that duplicate one another. Include '
    "only groups with 2+ members; omit singletons. Example: [[1,4],[7,9,10]]."
)


class DedupDecision(BaseModel):
    """Audit record for one story dropped as a near-duplicate of a kept story.

    Attributes:
        dropped_story_id: The story removed from the produce shortlist.
        kept_story_id: The cluster representative that was retained.
        cluster_story_ids: All story ids the judge grouped together.
        reason: Short machine reason (always ``"near_duplicate"`` today).

    Example:
        >>> d = DedupDecision(
        ...     dropped_story_id="s2", kept_story_id="s1",
        ...     cluster_story_ids=["s1", "s2"],
        ... )
        >>> d.kept_story_id
        's1'
    """

    dropped_story_id: str = Field(..., description="Story dropped as a duplicate")
    kept_story_id: str = Field(..., description="Cluster representative kept")
    cluster_story_ids: list[str] = Field(
        ..., description="All story ids the judge grouped as duplicates"
    )
    reason: str = Field(default="near_duplicate", description="Machine drop reason")


def _build_judge_prompt(stories: list[CanonicalStory]) -> str:
    """Render the numbered story catalog handed to the dedup judge.

    Args:
        stories: The produce shortlist, in order. Each story's 1-based ``n`` is its
            position in this list (the id the judge returns groups over).

    Returns:
        The user prompt: one numbered line per story with title, date, and a lead
        snippet.
    """
    lines: list[str] = [
        "Today's candidate stories (n. [date] TITLE — lead):",
        "",
    ]
    for index, story in enumerate(stories, start=1):
        published = story.canonical_published_utc
        date_label = published.date().isoformat() if published else "?"
        lead = (story.canonical_body_text or "").strip().replace("\n", " ")
        if len(lead) > _LEAD_SNIPPET_CHARS:
            lead = lead[:_LEAD_SNIPPET_CHARS].rstrip() + "…"
        lines.append(f"{index}. [{date_label}] {story.canonical_title} — {lead}")
    return "\n".join(lines)


def _representative_index(group_indices: list[int], stories: list[CanonicalStory]) -> int:
    """Pick which 0-based index in *group_indices* to KEEP (the rest are dropped).

    Deterministic (Rule 5): keep the highest-coverage story (``story_outlet_count``
    — the Importance signal), tiebreak by most recent publication, then by id so the
    same cluster always resolves the same way.

    Args:
        group_indices: 0-based indices into ``stories`` that the judge grouped.
        stories: The produce shortlist.

    Returns:
        The 0-based index to retain.
    """
    _epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def _sort_key(idx: int) -> tuple[int, float, str]:
        story = stories[idx]
        published = story.canonical_published_utc or _epoch
        return (
            story.story_outlet_count,
            published.timestamp(),
            story.canonical_story_id,
        )

    return max(group_indices, key=_sort_key)


def _parse_groups(
    raw_response: str, story_count: int
) -> list[list[int]]:
    """Parse + sanitize the judge's response into valid 0-based duplicate groups.

    Defensive against a hallucinating model: keeps only in-range integer ``n``
    values (converted to 0-based), drops out-of-range/duplicate members within a
    group, ignores groups with fewer than 2 surviving members, and ensures each
    story belongs to at most ONE group (first group wins) so a story is never
    dropped twice or assigned to conflicting clusters.

    Args:
        raw_response: The raw LLM text (JSON array of arrays of 1-based n values).
        story_count: Number of stories in the shortlist (range bound).

    Returns:
        Sanitized groups as lists of distinct in-range 0-based indices (size >= 2).
    """
    parsed = extract_json_from_llm_response(raw_response, stage="produce_dedup")
    if not isinstance(parsed, list):
        return []

    groups: list[list[int]] = []
    assigned: set[int] = set()
    for raw_group in parsed:
        if not isinstance(raw_group, list):
            continue
        members: list[int] = []
        seen_in_group: set[int] = set()
        for raw_member in raw_group:
            # Reason: accept ints or numeric strings; reject anything else.
            try:
                one_based = int(raw_member)
            except (TypeError, ValueError):
                continue
            zero_based = one_based - 1
            if zero_based < 0 or zero_based >= story_count:
                continue
            if zero_based in seen_in_group or zero_based in assigned:
                continue
            seen_in_group.add(zero_based)
            members.append(zero_based)
        if len(members) >= 2:
            assigned.update(members)
            groups.append(members)
    return groups


async def dedupe_produce_shortlist(
    stories: list[CanonicalStory],
    llm_client: object,
    *,
    model: str = DEDUP_JUDGE_MODEL,
    temperature: float = DEDUP_JUDGE_TEMPERATURE,
) -> tuple[list[CanonicalStory], list[DedupDecision]]:
    """Drop same-event / near-angle duplicates from the produce shortlist (LLM judge).

    One Gemini call clusters the shortlist into duplicate groups; for each group the
    code keeps the highest-coverage representative (:func:`_representative_index`)
    and drops the rest, so the expensive per-story generation never runs twice for
    one real story. Order of the kept stories is preserved.

    Fail-open: an empty/singleton shortlist returns immediately (no call); any LLM
    or parse error is logged and the ORIGINAL shortlist is returned unchanged — a
    dedup miss must never block the daily run (Rule 12).

    Args:
        stories: The produce shortlist (gate output), in order.
        llm_client: A client exposing ``async call_gemini(prompt, system, model,
            temperature) -> str`` (injected; mocked in tests).
        model: Gemini text model for the judge.
        temperature: Sampling temperature (low — clustering, not creativity).

    Returns:
        ``(kept_stories, decisions)`` — the de-duplicated shortlist (original order)
        and one :class:`DedupDecision` per dropped story.

    Example:
        >>> kept, decisions = await dedupe_produce_shortlist(pool, client)  # doctest: +SKIP
        >>> len(kept) <= len(pool)
        True
    """
    if len(stories) < 2:
        return list(stories), []

    prompt = _build_judge_prompt(stories)
    try:
        raw_response = await llm_client.call_gemini(  # type: ignore[attr-defined]
            prompt,
            system=DEDUP_JUDGE_SYSTEM,
            model=model,
            temperature=temperature,
        )
        groups = _parse_groups(raw_response, story_count=len(stories))
    except Exception as exc:  # noqa: BLE001 — fail-open: never block the daily run
        logger.error(
            "produce_dedup_failed_open",
            error_type=type(exc).__name__,
            error_message=str(exc)[:200],
            story_count=len(stories),
            fix_suggestion="Dedup judge errored; producing the un-deduped shortlist. "
            "Verify the Gemini key/quota and that the prompt yields a JSON array of groups.",
        )
        return list(stories), []

    drop_indices: set[int] = set()
    decisions: list[DedupDecision] = []
    for group in groups:
        keep_index = _representative_index(group, stories)
        kept_id = stories[keep_index].canonical_story_id
        cluster_ids = [stories[idx].canonical_story_id for idx in group]
        for idx in group:
            if idx == keep_index:
                continue
            drop_indices.add(idx)
            decisions.append(
                DedupDecision(
                    dropped_story_id=stories[idx].canonical_story_id,
                    kept_story_id=kept_id,
                    cluster_story_ids=cluster_ids,
                )
            )

    kept_stories = [
        story for index, story in enumerate(stories) if index not in drop_indices
    ]

    logger.info(
        "produce_dedup_completed",
        shortlist_count=len(stories),
        duplicate_group_count=len(groups),
        dropped_count=len(decisions),
        kept_count=len(kept_stories),
    )
    return kept_stories, decisions
