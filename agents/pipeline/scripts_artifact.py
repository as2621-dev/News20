"""Scripts-only review artifact — the founder gate before any paid reel (issue #68).

Founder decision (2026-07-25, staged production): the shortlist gate (#66/#67) stops
a run ABOVE script writing, which means the founder approves HEADLINES and only sees
the actual reels after paying for them. Scripts on ``gemini-3.5-flash`` are pennies;
TTS + posters are the expensive tail (the 2026-07-19 $24 burn was 58 reels' media).
So the ladder gained a middle rung: write the scripts, similarity-gate them, dump
them for review, and halt before the first TTS call.

This module builds that review artifact from the exact WRITE-wave output the render
wave would receive — same scripts, same order, plus the record of what the script
similarity gate dropped and why (a silent drop would be unreviewable).

Envelope shape mirrors the #67 shortlist artifact (``{run header, entries}``), so both
review files read the same way::

    {"scripts_run": {...}, "script_entries": [...], "script_dedup_drops": [...]}
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from agents.pipeline.models import WritePhaseResult
from agents.pipeline.produce_dedup import DedupDecision, script_spoken_text
from agents.shared.logger import get_logger

logger = get_logger("pipeline.scripts_artifact")


class ScriptEntry(BaseModel):
    """One written reel script, in founder-review form.

    Attributes:
        script_story_id: The canonical story id the reel narrates.
        script_headline: The headline the reel carries (the editorial view — what
            the poster and the feed would show, not the raw source title).
        script_segment_slug: The segment resolved ONCE in the write phase.
        script_resolved_category: The batch's resolve-once category verdict, or
            ``None`` when the caller supplied no verdict map.
        script_text: The full spoken transcript (``SPEAKER: text`` lines) — the
            SAME string the similarity judge read, so approval and judgment are of
            one artifact.
        script_word_count: Spoken word count (the runtime proxy).
    """

    script_story_id: str = Field(..., description="Canonical story id")
    script_headline: str = Field(..., description="Editorial headline of the reel")
    script_segment_slug: str = Field(default="", description="Resolve-once segment")
    script_resolved_category: str | None = Field(
        default=None, description="Batch resolve-once category verdict"
    )
    script_text: str = Field(..., description="Full spoken transcript")
    script_word_count: int = Field(default=0, ge=0, description="Spoken word count")


class ScriptsRunHeader(BaseModel):
    """Run-provenance header stamped onto the on-disk scripts artifact.

    Attributes:
        run_feed_date: ISO feed date the scripts were written for.
        run_semantic_relevance_mode: Which relevance mode selected the underlying
            stories — ``semantic`` / ``disabled`` / ``degraded`` (#67). Carried
            forward so a script review is as auditable as the shortlist review.
        run_written_script_count: Scripts in this artifact (post-gate).
        run_script_dedup_enabled: Whether the script similarity gate ran at all —
            zero drops means something different when the gate was off.
        run_script_dedup_dropped_count: Scripts the gate dropped as near-duplicates.
    """

    run_feed_date: str = Field(..., description="ISO feed date")
    run_semantic_relevance_mode: str = Field(
        ..., description="semantic | disabled | degraded"
    )
    run_written_script_count: int = Field(default=0, ge=0)
    run_script_dedup_enabled: bool = Field(
        default=False, description="Whether the script similarity gate ran"
    )
    run_script_dedup_dropped_count: int = Field(default=0, ge=0)


class ScriptsArtifact(BaseModel):
    """The ``.agents/scripts/<date>-scripts.json`` on-disk CONTRACT (issue #68).

    Envelope, not a bare list — same reason as the #67 shortlist artifact: a review
    file that cannot say HOW its contents were selected and what was dropped makes
    every later audit unfalsifiable.
    """

    scripts_run: ScriptsRunHeader = Field(..., description="Run provenance")
    script_entries: list[ScriptEntry] = Field(
        default_factory=list, description="The written scripts up for review"
    )
    script_dedup_drops: list[DedupDecision] = Field(
        default_factory=list,
        description="Near-duplicate scripts the gate dropped (with the kept twin)",
    )


def build_script_entries(
    write_results: Iterable[WritePhaseResult],
) -> list[ScriptEntry]:
    """Render the WRITE wave's output into founder-reviewable script entries.

    Args:
        write_results: The written reels (post similarity gate), in pool order.

    Returns:
        One :class:`ScriptEntry` per written reel, in the same order.

    Example:
        >>> entries = build_script_entries(write_results)  # doctest: +SKIP
        >>> entries[0].script_text.splitlines()[0]  # doctest: +SKIP
        'ALEX: Wait, so they just announced it?'
    """
    entries = [
        ScriptEntry(
            script_story_id=write_result.canonical_story_id,
            script_headline=write_result.editorial_story.canonical_title,
            script_segment_slug=write_result.segment_slug or "",
            script_resolved_category=write_result.resolved_category,
            script_text=script_spoken_text(write_result),
            script_word_count=write_result.script.word_count,
        )
        for write_result in write_results
    ]
    logger.info("script_entries_built", script_count=len(entries))
    return entries


def build_scripts_artifact(result: Any) -> ScriptsArtifact:
    """Wrap a pipeline result's scripts in their run-provenance envelope.

    Args:
        result: A ``DailyPipelineResult`` (duck-typed to keep this module free of a
            circular import — ``daily_batch`` imports this one).

    Returns:
        The :class:`ScriptsArtifact` to serialize with ``model_dump()``.

    Example:
        >>> artifact = build_scripts_artifact(result)  # doctest: +SKIP
        >>> artifact.scripts_run.run_script_dedup_dropped_count  # doctest: +SKIP
        1
    """
    return ScriptsArtifact(
        scripts_run=ScriptsRunHeader(
            run_feed_date=result.feed_date,
            run_semantic_relevance_mode=(
                result.semantic_relevance.semantic_relevance_mode
            ),
            run_written_script_count=len(result.scripts),
            run_script_dedup_enabled=result.script_dedup_enabled,
            run_script_dedup_dropped_count=len(result.script_dedup_drops),
        ),
        script_entries=list(result.scripts),
        script_dedup_drops=list(result.script_dedup_drops),
    )
