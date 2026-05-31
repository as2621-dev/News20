"""Shared JSON extraction utility for LLM responses.

PORTED verbatim from the TLDW donor
(``~/TLDW-Phase2/.../agents/pipeline/json_utils.py``). LLMs frequently wrap
valid JSON in markdown code fences (```json ... ```) or prepend/append
conversational text. This module provides a robust extraction function used by
the News20 scripting and verification stages.

Example:
    >>> from agents.pipeline.json_utils import extract_json_from_llm_response
    >>> data = extract_json_from_llm_response('```json\\n[{"a": 1}]\\n```', stage="scripting")
    >>> data
    [{'a': 1}]
"""

from __future__ import annotations

import json
import re
from typing import Any

from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger

logger = get_logger("pipeline.json_utils")

# Reason: DOTALL so the captured group can span multiple lines.
# Matches ```json ... ``` or ``` ... ``` with optional whitespace.
_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n\s*```", re.DOTALL)


def extract_json_from_llm_response(raw_response: str, stage: str) -> Any:
    """Extract and parse JSON from an LLM response string.

    Uses a three-tier strategy:
        1. Direct ``json.loads`` on the stripped response (fast path).
        2. Regex extraction of content inside markdown code fences.
        3. Bracket-boundary extraction (first ``[`` or ``{`` to last ``]`` or ``}``).

    Args:
        raw_response: Raw text response from an LLM call.
        stage: Pipeline stage name (for error messages and logging).

    Returns:
        Parsed JSON value (typically a list or dict).

    Raises:
        PipelineStageError: If the response cannot be parsed as JSON after
            all extraction strategies are exhausted.

    Example:
        >>> extract_json_from_llm_response('[{"speaker": "ALEX", "text": "hi"}]', "scripting")
        [{'speaker': 'ALEX', 'text': 'hi'}]
    """
    text = raw_response.strip()

    if not text:
        raise PipelineStageError(
            stage=stage,
            message="LLM returned an empty response",
            fix_suggestion="Check LLM API key, model availability, and prompt formatting",
        )

    # Tier 1: direct parse (covers bare JSON responses)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Tier 2: regex extraction from markdown code fences
    fence_match = _FENCE_PATTERN.search(text)
    if fence_match:
        fenced_content = fence_match.group(1).strip()
        try:
            return json.loads(fenced_content)
        except json.JSONDecodeError:
            pass

    # Tier 3: bracket-boundary extraction (first [ or { to last ] or })
    first_bracket = _find_first_json_start(text)
    if first_bracket >= 0:
        opening = text[first_bracket]
        closing = "]" if opening == "[" else "}"
        last_bracket = text.rfind(closing)
        if last_bracket > first_bracket:
            candidate = text[first_bracket : last_bracket + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # All strategies exhausted
    logger.error(
        "json_extraction_failed",
        stage=stage,
        response_preview=raw_response[:500],
        fix_suggestion="LLM response is not valid JSON; check prompt formatting",
    )
    raise PipelineStageError(
        stage=stage,
        message="Failed to parse LLM response as JSON after all extraction strategies",
        fix_suggestion="Check LLM response format; ensure prompt requests valid JSON output",
    )


def _find_first_json_start(text: str) -> int:
    """Find the index of the first ``[`` or ``{`` in *text*.

    Returns:
        The index, or ``-1`` if neither character is found.
    """
    idx_arr = text.find("[")
    idx_obj = text.find("{")
    candidates = [i for i in (idx_arr, idx_obj) if i >= 0]
    return min(candidates) if candidates else -1
