"""Pool-level cross-reel diversity review of the day's reel scripts (LLM showrunner).

WHY THIS STAGE EXISTS
---------------------
Every reel script is generated in TOTAL ISOLATION (``run_single_source_scripting``
sees one story, never the others). With nothing to differentiate against, the model
converges on the same conversational scaffolding across the whole pool — the same
opener shape ("Wait, what?"), the same reaction words, the same handoff. A listener
going through all 30 reels hears the sameness as a robotic tell, even though each
reel in isolation is fine.

Per-reel deterministic rotation (``scripting.OPENER_ARCHETYPES`` /
``HANDOFF_STYLES``) spreads the SHAPES; this stage is the holistic backstop: once
every reel is written, one showrunner reads them SIDE BY SIDE and rewrites the
conversational scaffolding that still repeats — something no single-reel pass can
see. It runs at the production-POOL level (the shared pool fanned out to every
user's feed), AFTER write + verify but BEFORE any TTS, so the cheap text edit lands
before the expensive audio render.

WHAT IT MAY AND MAY NOT TOUCH
-----------------------------
The pass rewrites ONLY non-factual conversational scaffolding — openers, reaction
interjections, and handoffs. Every turn that asserts a fact/number/name/date/quote
is FROZEN. Two guards enforce this: (1) a freeze guard drops any revision whose
echoed ``original_text`` does not match the live turn (so a mis-indexed edit can't
land), and (2) every reel the pass modifies is RE-VERIFIED against its original
source — if the rewrite introduced anything ungrounded, the reel REVERTS to its
pre-revision script (which already passed verification at write time). A revised
reel can therefore never publish ungrounded content.

FAIL-OPEN
---------
Diversity is a quality nicety, not a correctness gate — it must never crash or block
the daily run (Rule 12). Any LLM/parse error on a chunk is logged loudly and that
chunk's reels are left unchanged. A pool of fewer than two reels returns immediately.

The Gemini SDK is mocked at the ``llm_client`` boundary in tests — no live call,
no cost (CLAUDE.md mocking mandate).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from agents.pipeline.json_utils import extract_json_from_llm_response
from agents.pipeline.models import DialogueTurn, WritePhaseResult
from agents.pipeline.stages.scripting import SPOKEN_WPM
from agents.pipeline.stages.verification import run_single_source_verification
from agents.shared.exceptions import VerificationHaltError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.stages.batch_review")

# Reason: pin the same proven text model the other SP2 stages use; one judge over
# a chunk of the day's reels is cheap relative to the TTS it precedes.
BATCH_REVIEW_MODEL = "gemini-2.5-flash"

# Reason: a touch warmer than the dedup judge's 0.1 — this pass not only DETECTS
# repetition but writes the fresh replacement scaffolding, which needs a little
# lexical variety. Still low: the edits are short and tightly scoped.
BATCH_REVIEW_TEMPERATURE = 0.4

# Reason: bound the prompt size. Cross-reel sameness is mostly local within the
# pool ordering, so reviewing in chunks of this many reels keeps each prompt small
# while still catching the pervasive repetition the listener actually notices.
_MAX_REELS_PER_CHUNK = 25

_VALID_TURN_ROLES = {"opener", "reaction", "handoff"}

BATCH_REVIEW_SYSTEM = (
    "You are the showrunner for News20, an audio news app. You are reading the day's "
    "two-host (ALEX/JORDAN) reels SIDE BY SIDE, looking for one problem only: the "
    "reels reuse the same CONVERSATIONAL SCAFFOLDING, so a listener going through "
    "many of them hears the same tics on repeat. Scaffolding means turns that carry "
    "NO facts: the OPENER hook, pure REACTION interjections (e.g. 'oh wow', 'no way', "
    "'hold on'), and the closing HANDOFF to the next story. Find scaffolding that "
    "repeats ACROSS reels — the same opener shape, the same reaction word, the same "
    "handoff phrasing — and rewrite just those turns so each reel feels distinct and "
    "natural.\n\n"
    "HARD RULES:\n"
    "- FREEZE every turn that states a fact, number, date, name, organization, quote, "
    "or specific event. Never rewrite those. When unsure whether a turn carries a "
    "fact, leave it ALONE.\n"
    "- Only rewrite turns whose role is opener, reaction, or handoff.\n"
    "- Keep the SAME speaker for the turn. Keep it plain, speakable US English, at "
    "most about 140 characters, no stage directions, no emojis.\n"
    "- Do not invent new facts. Scaffolding carries none; keep it that way.\n\n"
    "Return ONLY a JSON array. Each element rewrites ONE turn and has EXACTLY these "
    'keys: {"reel_n": <int>, "turn_index": <int>, "original_text": "<verbatim '
    'current text>", "revised_text": "<your rewrite>", "turn_role": '
    '"opener"|"reaction"|"handoff"}. "reel_n" is the reel\'s number as shown; '
    '"turn_index" is the [bracketed] index of the turn within that reel; '
    '"original_text" must copy the current turn text verbatim. Omit any reel or turn '
    "that needs no change. If nothing repeats, return []."
)


class ReelTurnRevision(BaseModel):
    """One targeted rewrite of a single non-factual scaffolding turn.

    Attributes:
        reel_n: 1-based reel number AS SHOWN in the prompt chunk (remapped to the
            global pool index by the caller).
        turn_index: 0-based index of the turn within that reel's ``script.turns``.
        original_text: The model's verbatim echo of the current turn text — matched
            against the live turn as a freeze guard (a mismatch drops the revision).
        revised_text: The fresh scaffolding text to swap in.
        turn_role: Which scaffolding role the model classified the turn as.

    Example:
        >>> r = ReelTurnRevision(
        ...     reel_n=2, turn_index=0, original_text="Wait, what?",
        ...     revised_text="Okay, this one's strange.", turn_role="opener",
        ... )
        >>> r.turn_role
        'opener'
    """

    reel_n: int = Field(..., ge=1, description="1-based reel number as shown in chunk")
    turn_index: int = Field(..., ge=0, description="0-based turn index within the reel")
    original_text: str = Field(
        ..., description="Verbatim echo of the current turn text"
    )
    revised_text: str = Field(
        ..., min_length=1, description="Fresh scaffolding text to swap in"
    )
    turn_role: Literal["opener", "reaction", "handoff"] = Field(
        ..., description="Scaffolding role the model classified the turn as"
    )


class BatchReviewResult(BaseModel):
    """Audit summary for one pool-level review pass.

    Attributes:
        reels_reviewed: How many reels the pass read.
        reels_modified: How many reels kept at least one rewrite (post re-verify).
        reels_reverted: How many reels were reverted because a rewrite failed
            re-verification (or the re-verify call errored).

    Example:
        >>> BatchReviewResult(reels_reviewed=10, reels_modified=3).reels_modified
        3
    """

    reels_reviewed: int = Field(default=0, ge=0)
    reels_modified: int = Field(default=0, ge=0)
    reels_reverted: int = Field(default=0, ge=0)


def _build_review_prompt(chunk: list[WritePhaseResult]) -> str:
    """Render the numbered side-by-side reel catalog handed to the showrunner.

    Args:
        chunk: A slice of the survivor pool. Each reel's 1-based number is its
            position in this chunk (the ``reel_n`` the model returns edits over);
            each turn is shown with its 0-based ``[turn_index]``.

    Returns:
        The user prompt: one block per reel, each turn as ``[i] SPEAKER: text``.
    """
    lines: list[str] = [
        "Today's reels (review the conversational scaffolding across all of them):",
        "",
    ]
    for reel_number, write_result in enumerate(chunk, start=1):
        lines.append(f"Reel {reel_number}:")
        for turn_index, turn in enumerate(write_result.script.turns):
            lines.append(f"  [{turn_index}] {turn.speaker}: {turn.text}")
        lines.append("")
    return "\n".join(lines)


def _parse_revisions(
    raw_response: str, chunk: list[WritePhaseResult]
) -> list[ReelTurnRevision]:
    """Parse + sanitize the showrunner's response into valid, applicable revisions.

    Defensive against a hallucinating model: keeps only revisions whose ``reel_n``
    and ``turn_index`` are in range, whose ``turn_role`` is a known scaffolding
    role, and whose ``original_text`` MATCHES the live turn text (the freeze guard
    — a mismatch means the model is pointing at a turn that moved or never existed,
    so the edit is dropped rather than risking a wrong turn). At most one revision
    per ``(reel_n, turn_index)`` survives (first wins).

    Args:
        raw_response: The raw LLM text (JSON array of revision objects).
        chunk: The reels shown to the model (range + freeze-guard source).

    Returns:
        Sanitized, directly-applicable :class:`ReelTurnRevision` objects.
    """
    parsed = extract_json_from_llm_response(raw_response, stage="batch_review")
    if not isinstance(parsed, list):
        return []

    revisions: list[ReelTurnRevision] = []
    seen: set[tuple[int, int]] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            reel_n = int(entry.get("reel_n"))
            turn_index = int(entry.get("turn_index"))
        except (TypeError, ValueError):
            continue
        role = str(entry.get("turn_role", "")).strip().lower()
        original_text = str(entry.get("original_text", ""))
        revised_text = str(entry.get("revised_text", "")).strip()
        if role not in _VALID_TURN_ROLES or not revised_text:
            continue
        if reel_n < 1 or reel_n > len(chunk):
            continue
        turns = chunk[reel_n - 1].script.turns
        if turn_index < 0 or turn_index >= len(turns):
            continue
        # Reason: freeze guard — only apply the edit if the model's echoed text
        # matches the actual turn, so a mis-indexed edit can never overwrite a
        # different (possibly factual) turn.
        if original_text.strip() != turns[turn_index].text.strip():
            continue
        key = (reel_n, turn_index)
        if key in seen:
            continue
        seen.add(key)
        revisions.append(
            ReelTurnRevision(
                reel_n=reel_n,
                turn_index=turn_index,
                original_text=original_text,
                revised_text=revised_text,
                turn_role=role,  # type: ignore[arg-type]
            )
        )
    return revisions


def _apply_revisions(
    write_result: WritePhaseResult, turn_edits: dict[int, str]
) -> WritePhaseResult:
    """Return a copy of *write_result* with the given turn texts swapped in.

    Only ``script.turns`` (and the derived word-count/duration estimates) change;
    the speaker of each edited turn is preserved, and ``editorial_story`` /
    ``original_story`` are untouched.

    Args:
        write_result: The reel to revise.
        turn_edits: ``{turn_index: revised_text}`` for this reel.

    Returns:
        A new :class:`WritePhaseResult` carrying the revised script.
    """
    new_turns = list(write_result.script.turns)
    for turn_index, revised_text in turn_edits.items():
        existing = new_turns[turn_index]
        new_turns[turn_index] = DialogueTurn(
            speaker=existing.speaker, text=revised_text
        )
    word_count = sum(len(turn.text.split()) for turn in new_turns)
    revised_script = write_result.script.model_copy(
        update={
            "turns": new_turns,
            "word_count": word_count,
            "estimated_duration_seconds": int((word_count / SPOKEN_WPM) * 60),
        }
    )
    return write_result.model_copy(update={"script": revised_script})


async def review_reel_pool(
    survivors: list[WritePhaseResult],
    llm_client: object,
    *,
    model: str = BATCH_REVIEW_MODEL,
    temperature: float = BATCH_REVIEW_TEMPERATURE,
) -> list[WritePhaseResult]:
    """Diversify repetitive cross-reel scaffolding across the whole reel pool.

    One Gemini call per chunk reads the reels side by side and returns targeted
    rewrites of repeating opener/reaction/handoff turns. Each rewrite is applied
    only if it passes the freeze guard (:func:`_parse_revisions`), and every reel
    that ends up modified is RE-VERIFIED against its original source — a reel whose
    rewrite fails re-verification (or whose re-verify call errors) REVERTS to its
    pre-revision script, so a revised reel can never publish ungrounded content.

    Fail-open: a pool of fewer than two reels returns immediately; any LLM/parse
    error on a chunk is logged and that chunk's reels are left unchanged (Rule 12).
    Order is always preserved.

    Args:
        survivors: The write-phase survivors for the day's production pool, in order.
        llm_client: A client exposing ``async call_gemini(prompt, system, model,
            temperature) -> str`` (injected; mocked in tests). Also used for the
            re-verification calls.
        model: Gemini text model for the showrunner.
        temperature: Sampling temperature for the rewrites.

    Returns:
        The (possibly revised) survivor list, same length and order as the input.

    Example:
        >>> revised = await review_reel_pool(survivors, client)  # doctest: +SKIP
        >>> len(revised) == len(survivors)
        True
    """
    if len(survivors) < 2:
        return list(survivors)

    # ── 1. Collect revisions chunk by chunk (fail-open per chunk) ──
    edits_by_reel: dict[int, dict[int, str]] = {}
    for chunk_start in range(0, len(survivors), _MAX_REELS_PER_CHUNK):
        chunk = survivors[chunk_start : chunk_start + _MAX_REELS_PER_CHUNK]
        try:
            prompt = _build_review_prompt(chunk)
            raw_response = await llm_client.call_gemini(  # type: ignore[attr-defined]
                prompt,
                system=BATCH_REVIEW_SYSTEM,
                model=model,
                temperature=temperature,
            )
            revisions = _parse_revisions(raw_response, chunk)
        except Exception as exc:  # noqa: BLE001 — fail-open: never block the daily run
            logger.error(
                "batch_review_failed_open",
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
                chunk_start=chunk_start,
                chunk_size=len(chunk),
                fix_suggestion="Review judge errored; leaving this chunk's reels "
                "unchanged. Verify the Gemini key/quota and the JSON-array contract.",
            )
            continue
        for revision in revisions:
            global_index = chunk_start + (revision.reel_n - 1)
            edits_by_reel.setdefault(global_index, {})[revision.turn_index] = (
                revision.revised_text
            )

    if not edits_by_reel:
        logger.info(
            "batch_review_completed",
            reels_reviewed=len(survivors),
            reels_modified=0,
            reels_reverted=0,
        )
        return list(survivors)

    # ── 2. Apply edits to a working copy of the pool ──
    result = list(survivors)
    for index, turn_edits in edits_by_reel.items():
        result[index] = _apply_revisions(survivors[index], turn_edits)
    modified_indices = list(edits_by_reel.keys())

    # ── 3. Re-verify every modified reel; revert any that no longer grounds ──
    async def _reverify(index: int) -> tuple[int, bool]:
        revised = result[index]
        try:
            await run_single_source_verification(
                script=revised.script,
                source_story=revised.original_story,
                llm_client=llm_client,
            )
            return index, True
        except VerificationHaltError:
            logger.warning(
                "batch_review_revert_ungrounded",
                story_id=revised.canonical_story_id,
                fix_suggestion="A scaffolding rewrite made the reel ungrounded; "
                "reverting to the pre-revision (already-verified) script.",
            )
            return index, False
        except Exception as exc:  # noqa: BLE001 — fail-open: revert on any re-verify error
            logger.error(
                "batch_review_reverify_failed_open",
                story_id=revised.canonical_story_id,
                error_type=type(exc).__name__,
                error_message=str(exc)[:200],
                fix_suggestion="Re-verification errored; reverting the reel to its "
                "pre-revision script to stay safe.",
            )
            return index, False

    verdicts = await asyncio.gather(*(_reverify(i) for i in modified_indices))
    reverted = 0
    for index, grounded in verdicts:
        if not grounded:
            result[index] = survivors[index]
            reverted += 1

    logger.info(
        "batch_review_completed",
        reels_reviewed=len(survivors),
        reels_modified=len(modified_indices) - reverted,
        reels_reverted=reverted,
    )
    return result
