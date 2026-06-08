"""Parse + validate raw LLM candidate output for the catalog generator (5f SP1).

Split out of ``scripts/seed_catalog/generate_candidates.py`` to keep that module
focused on orchestration + I/O + CLI (file-size discipline). This module owns the
two boundary-hardening concerns:

  1. **Parsing** an untrusted chat-model text response into a JSON array
     (``parse_candidate_array``) — tolerant of code fences and surrounding prose.
  2. **Validating + normalising** each raw candidate against the seeder's
     structural contract (``normalise_candidate``) — identity field present, the
     ``topic_tags`` constrained to the 8 valid keys with ``topic_tags[0]`` a valid
     key, optional axis fields carried, stray keys stripped.

These are pure functions (no network, no model call) so they unit-test in
isolation. The model call itself lives in the generator; the resolvers
(existence verification) live in the seeder.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agents.shared.logger import get_logger
from scripts.seed_catalog.seed_catalog import ALLOWED_ARCHETYPES, ALLOWED_TOPIC_TAGS

logger = get_logger("seed_catalog.candidate_validation")

# Per-axis identity field used both as the dedup key and the "is this candidate
# structurally complete?" check. Mirrors seed_catalog._dedup_key_fn semantics.
IDENTITY_FIELD: dict[str, str] = {
    "channels": "youtube_handle",
    "podcasts": "search_term",
    "x": "handle",
    "personalities": "display_name",
}


# ── Robust model-output parsing ────────────────────────────────────────────────


def parse_candidate_array(model_text: str) -> list[dict[str, Any]]:
    """Parse a model's text response into a list of candidate objects.

    Tolerant of the common ways a chat model wraps JSON: leading/trailing prose,
    a ```json fenced block, or a bare array. Returns an empty list (never raises)
    when no JSON array can be recovered, so a single bad cell cannot abort a batch
    — the caller logs the empty result and moves on.

    Args:
        model_text: The raw text returned by ``LLMClient.call_gemini``.

    Returns:
        The parsed list of dict candidates, or ``[]`` when nothing parses.

    Example:
        >>> parse_candidate_array('```json\\n[{"youtube_handle": "x"}]\\n```')
        [{'youtube_handle': 'x'}]
    """
    if not model_text or not model_text.strip():
        return []

    cleaned = _strip_code_fences(model_text.strip())

    # Reason: try a direct parse first (the happy path — strict JSON array).
    parsed = _try_json_loads(cleaned)
    if parsed is not None:
        return parsed

    # Reason: fall back to extracting the first bracketed array from surrounding
    # prose (model occasionally prepends a sentence despite the instruction).
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        parsed = _try_json_loads(match.group(0))
        if parsed is not None:
            return parsed

    logger.error(
        "generate_candidates_unparseable_model_output",
        text_preview=cleaned[:200],
        fix_suggestion="Model did not return a JSON array; re-run the cell or lower temperature.",
    )
    return []


def _strip_code_fences(text: str) -> str:
    """Strip a leading ```json / ``` fence and a trailing ``` fence if present.

    Args:
        text: The (already-stripped) model text.

    Returns:
        The text with surrounding markdown code fences removed.
    """
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def _try_json_loads(text: str) -> list[dict[str, Any]] | None:
    """Attempt ``json.loads`` and confirm the result is a list of dicts.

    Args:
        text: A candidate JSON string.

    Returns:
        The parsed list when it decodes to a list of dicts, else None.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


# ── Per-candidate validation + normalisation ───────────────────────────────────


def normalise_candidate(raw: dict[str, Any], entry_type: str) -> dict[str, Any] | None:
    """Validate + normalise one raw candidate for its axis, or None to drop it.

    Enforces the seeder's structural contract:
      - the axis identity field is present and non-empty,
      - ``topic_tags`` is non-empty and ``topic_tags[0]`` is one of the 8 keys
        (when position 0 is invalid but a later tag IS a valid key, that key is
        promoted to position 0 — a cheap coerce rather than a drop),
      - every retained tag is one of the 8 keys (unknown tags are stripped).

    Args:
        raw: One raw candidate object from the model.
        entry_type: The axis (``channels`` / ``podcasts`` / ``x`` / ``personalities``).

    Returns:
        A cleaned candidate dict carrying only the schema fields, or None to drop.
    """
    identity_field = IDENTITY_FIELD[entry_type]
    identity_value = raw.get(identity_field)
    if not isinstance(identity_value, str) or not identity_value.strip():
        return None

    valid_tags = _normalise_topic_tags(raw.get("topic_tags"))
    if not valid_tags:
        return None

    candidate: dict[str, Any] = {
        identity_field: identity_value.strip(),
        "topic_tags": valid_tags,
    }
    _carry_optional_fields(candidate, raw, entry_type)
    return candidate


def _normalise_topic_tags(raw_tags: Any) -> list[str]:
    """Filter ``topic_tags`` to the 8 valid keys, keeping order, deduped.

    The first valid key is promoted to position 0 (so a candidate whose declared
    position-0 tag is invalid but which carries a valid key elsewhere is coerced,
    not dropped). Returns ``[]`` when no valid key is present (→ drop the candidate).

    Args:
        raw_tags: The candidate's raw ``topic_tags`` value (any type).

    Returns:
        An ordered, deduped list of valid 8-key tags (possibly empty).
    """
    if not isinstance(raw_tags, list):
        return []
    seen: set[str] = set()
    valid: list[str] = []
    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        normalized = tag.strip().lower()
        if normalized in ALLOWED_TOPIC_TAGS and normalized not in seen:
            seen.add(normalized)
            valid.append(normalized)
    return valid


def _carry_optional_fields(
    candidate: dict[str, Any], raw: dict[str, Any], entry_type: str
) -> None:
    """Copy axis-specific optional fields (and ``personas``) onto a clean candidate.

    Only known, typed fields are carried — this strips any stray keys the model
    might hallucinate while preserving the schema's optional fields.

    Args:
        candidate: The clean candidate being built (mutated in place).
        raw: The raw model candidate.
        entry_type: The axis.
    """
    if entry_type == "x":
        source_name = raw.get("source_name")
        if isinstance(source_name, str) and source_name.strip():
            candidate["source_name"] = source_name.strip()
    elif entry_type == "personalities":
        slug = raw.get("wikipedia_slug")
        if isinstance(slug, str) and slug.strip():
            candidate["wikipedia_slug"] = slug.strip()
        aliases = raw.get("aliases")
        if isinstance(aliases, list):
            clean_aliases = [
                a.strip() for a in aliases if isinstance(a, str) and a.strip()
            ]
            if clean_aliases:
                candidate["aliases"] = clean_aliases

    # Optional cross-archetype multi-tag (carried for any axis).
    personas = raw.get("personas")
    if isinstance(personas, list):
        clean_personas = [
            p.strip()
            for p in personas
            if isinstance(p, str) and p.strip() in ALLOWED_ARCHETYPES
        ]
        if clean_personas:
            candidate["personas"] = clean_personas


def candidate_dedup_key(candidate: dict[str, Any], entry_type: str) -> str:
    """Return the lowercased identity key for in-cell dedup.

    Mirrors ``seed_catalog._dedup_key_fn`` (the X handle drops its leading @) so
    the generator dedupes on the SAME identity the seeder will later upsert on.

    Args:
        candidate: A normalised candidate.
        entry_type: The axis.

    Returns:
        The lowercased dedup key.
    """
    value = str(candidate.get(IDENTITY_FIELD[entry_type]) or "")
    if entry_type == "x":
        value = value.lstrip("@")
    return value.strip().lower()
