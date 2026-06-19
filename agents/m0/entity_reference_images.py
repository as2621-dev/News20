"""Fetch + verify + cache the canonical reference photo for a resolved person (L5).

Phase 0c, Sub-phase 3. Given a resolved person (the name the story text actually
names, not a role), this module returns a VERIFIED, CURRENT reference photo — the
trusted face the image model is later conditioned on (SP4 consumes the result).

Flow of :func:`get_or_fetch_entity_reference_image`:

  1. Look up the cached row by ``entity_key``. If present AND fresh (verified within
     ``REFERENCE_REFRESH_DAYS``), return it with **zero network calls**.
  2. On a miss or a stale row: SERP-search the EXACT name + recent year
     (``serper_image_search.search_images``), download candidates
     (``download_candidates.download_candidate``).
  3. Run a Gemini-Flash identity-VERIFICATION pass over each candidate — "is this
     {name}, and a CURRENT likeness (not a former office-holder / different
     person)?" — and pick the highest-confidence match.
  4. If the best confidence clears ``REFERENCE_MIN_CONFIDENCE``: upload the bytes
     to the ``entity-reference-images`` bucket and UPSERT the row (on conflict
     ``entity_key``), then return the :class:`ReferenceImage`.
  5. If NOTHING clears the threshold: return ``None`` and write NOTHING — no row,
     no upload. This is the safety guarantee: never cache a wrong/low-confidence
     face (the image model is never the source of truth for WHO a person is).

The image model is never the identity source of truth: the story text supplies the
name, this store supplies the face. Reuses the existing SERP / download / Gemini
building blocks — no duplicated search, download, or client logic.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from google import genai
from pydantic import BaseModel, Field

from agents.m0.download_candidates import download_candidate
from agents.m0.poster_models import CANDIDATE_LIMIT, GEMINI_LLM_MODEL
from agents.m0.serper_image_search import search_images
from agents.shared.logger import get_logger

logger = get_logger("m0.entity_reference_images")

# ── Env-tunable knobs (see plans/phase-0c §Open questions) ────────────────────
# Reason: a fetched photo is only cached when the Flash verifier is at least this
# confident it is the named person AND a current likeness. Below it we cache
# NOTHING rather than risk a wrong face. Override at run time via env.
REFERENCE_MIN_CONFIDENCE: float = float(os.environ.get("REFERENCE_MIN_CONFIDENCE", "0.7"))
# Reason: a cached row older than this (by verified_at) is treated as stale and
# re-fetched — a person who changes role inside the window may serve a slightly
# stale photo until refresh (accepted for v1).
REFERENCE_REFRESH_DAYS: int = int(os.environ.get("REFERENCE_REFRESH_DAYS", "30"))

REFERENCE_BUCKET: str = "entity-reference-images"
_REFERENCE_CONTENT_TYPE: str = "image/jpeg"
# Reason: a fixed object name per entity so a re-fetch upserts the SAME object
# (mirrors the deterministic ``{story_id}/poster.webp`` path in fill_batch_posters).
_REFERENCE_OBJECT_NAME: str = "reference.jpg"


class ReferenceImage(BaseModel):
    """A verified canonical reference photo for one resolved entity.

    Mirrors the ``entity_reference_images`` table (migration 0019). Returned to SP4,
    which conditions the image generator on ``reference_public_url`` / the stored bytes.
    """

    reference_id: str | None = Field(default=None, description="Row uuid (None until the DB assigns it on insert)")
    entity_key: str = Field(..., description="Normalized resolved person name — the unique lookup key")
    entity_kind: str = Field(default="person", description="Resolved entity kind (normally 'person')")
    reference_storage_path: str = Field(..., description="Object path inside the entity-reference-images bucket")
    reference_public_url: str = Field(..., description="Public CDN URL of the uploaded reference photo")
    source_page_url: str | None = Field(default=None, description="Page the winning image was found on")
    verified_at: str = Field(..., description="ISO timestamp the photo was verified/cached")
    valid_as_of: str | None = Field(default=None, description="Story date the photo was accepted as current for")
    verification_confidence: float = Field(..., description="Flash identity-verification score (0..1)")


class _VerificationResponse(BaseModel):
    """Structured identity-verification verdict the Flash model must return."""

    is_match: bool = Field(description="True only if the image clearly shows the named person")
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 confidence the image is the named CURRENT person")
    rationale: str = Field(default="", description="One sentence justifying the verdict")


def _verification_instruction(entity_name: str, as_of: str | None) -> str:
    """Build the identity-verification prompt for the Flash multimodal call.

    Args:
        entity_name: The exact resolved person name to verify against.
        as_of: ISO story date the likeness must be current for (may be None).

    Returns:
        The instruction string sent alongside the candidate image bytes.
    """
    currency_clause = (
        f" The likeness must be CURRENT as of {as_of} — reject a former office-holder "
        "or an older photo of a different person who once held the role."
        if as_of
        else " The likeness must be a CURRENT, recent photo — reject a former office-holder."
    )
    return (
        f"Is the attached image a photograph of {entity_name}?{currency_clause} "
        "Set is_match true ONLY if you are confident it clearly depicts that specific, "
        "correct, current person — not a different person, not a former incumbent of the "
        "same role, not a caricature/illustration. Return confidence 0..1 and a one-line "
        "rationale. Return JSON only."
    )


def _is_row_fresh(verified_at_raw: str | None, now: datetime) -> bool:
    """True if a cached row's ``verified_at`` is within ``REFERENCE_REFRESH_DAYS``.

    Args:
        verified_at_raw: The row's ``verified_at`` ISO string (or None).
        now: The current time (injected so callers/tests stay deterministic).

    Returns:
        True when the row is fresh enough to serve without a re-fetch.

    Example:
        >>> from datetime import datetime, UTC
        >>> _is_row_fresh("2026-06-18T00:00:00+00:00", datetime(2026, 6, 19, tzinfo=UTC))
        True
    """
    if not verified_at_raw:
        return False
    try:
        verified_at = datetime.fromisoformat(verified_at_raw.replace("Z", "+00:00"))
    except ValueError:
        # Reason: an unparseable timestamp is treated as stale so we re-verify
        # rather than serve a row we cannot age-check.
        return False
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=UTC)
    age_days = (now - verified_at).total_seconds() / 86400.0
    return age_days < REFERENCE_REFRESH_DAYS


def _row_to_reference_image(row: dict[str, Any]) -> ReferenceImage:
    """Map a raw DB row dict to a :class:`ReferenceImage`."""
    return ReferenceImage(
        reference_id=str(row["reference_id"]) if row.get("reference_id") is not None else None,
        entity_key=row["entity_key"],
        entity_kind=row.get("entity_kind") or "person",
        reference_storage_path=row["reference_storage_path"],
        reference_public_url=row["reference_public_url"],
        source_page_url=row.get("source_page_url"),
        verified_at=str(row["verified_at"]),
        valid_as_of=str(row["valid_as_of"]) if row.get("valid_as_of") else None,
        verification_confidence=float(row.get("verification_confidence") or 0.0),
    )


def _lookup_cached_row(entity_key: str, supabase_client: Any) -> dict[str, Any] | None:
    """Read the single cached row for ``entity_key`` (or None)."""
    response = (
        supabase_client.table("entity_reference_images").select("*").eq("entity_key", entity_key).limit(1).execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def _reference_year(as_of: str | None, current_year: int | None) -> int | None:
    """Resolve the year to bias the SERP query toward a CURRENT photo (PURE).

    Prefers the explicit ``current_year``; otherwise the year of ``as_of``;
    otherwise None (no year appended). No nondeterministic clock call here — the
    caller injects the year so tests stay deterministic.

    Args:
        as_of: ISO story date (YYYY-MM-DD) or None.
        current_year: An explicitly supplied year (wins when present).

    Returns:
        The 4-digit year to append to the query, or None.

    Example:
        >>> _reference_year("2026-02-02", None)
        2026
        >>> _reference_year(None, 2027)
        2027
    """
    if current_year is not None:
        return current_year
    if as_of and len(as_of) >= 4 and as_of[:4].isdigit():
        return int(as_of[:4])
    return None


def _verify_candidate(
    image_bytes: bytes,
    mime_type: str,
    entity_name: str,
    as_of: str | None,
    genai_client: genai.Client,
) -> _VerificationResponse:
    """Run one Flash identity-verification pass over a candidate's bytes.

    Mirrors the multimodal call style in ``image_scorer._score_one`` (same model id,
    image-part + text-part, JSON response schema). A failed call returns a no-match
    zero-confidence verdict so a single bad candidate never aborts the fetch.

    Args:
        image_bytes: The candidate image bytes.
        mime_type: The candidate's mime type.
        entity_name: The exact person name to verify against.
        as_of: ISO story date the likeness must be current for (or None).
        genai_client: The Gemini client.

    Returns:
        The structured verification verdict (is_match + confidence).
    """
    prompt = _verification_instruction(entity_name, as_of)
    try:
        response = genai_client.models.generate_content(
            model=GEMINI_LLM_MODEL,
            contents=[
                genai.types.Content(
                    role="user",
                    parts=[
                        genai.types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        genai.types.Part(text=prompt),
                    ],
                )
            ],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_VerificationResponse,
            ),
        )
        verdict: _VerificationResponse = response.parsed  # type: ignore[assignment]  # SDK validates the schema
        return verdict
    except Exception as exc:  # noqa: BLE001 — one bad verification scores 0, never kills the fetch.
        logger.error(
            "reference_verification_failed",
            entity_name=entity_name,
            error_type=type(exc).__name__,
            error_message=str(exc),
            fix_suggestion="This candidate scored no-match/0 so the fetch continues; check GEMINI_API_KEY/quota.",
        )
        return _VerificationResponse(is_match=False, confidence=0.0, rationale="verification call failed")


def _upload_reference(supabase_client: Any, entity_key: str, image_bytes: bytes) -> tuple[str, str]:
    """Upsert the verified photo into the bucket; return (object_path, public_url).

    Mirrors ``fill_batch_posters._upload_poster`` (upsert upload + get_public_url).
    """
    object_path = f"{entity_key}/{_REFERENCE_OBJECT_NAME}"
    storage = supabase_client.storage.from_(REFERENCE_BUCKET)
    storage.upload(
        path=object_path,
        file=image_bytes,
        file_options={"content-type": _REFERENCE_CONTENT_TYPE, "upsert": "true"},
    )
    return object_path, storage.get_public_url(object_path)


async def get_or_fetch_entity_reference_image(
    entity_key: str,
    entity_name: str,
    entity_kind: str,
    as_of: str | None,
    supabase_client: Any,
    genai_client: genai.Client,
    *,
    current_year: int | None = None,
    now: datetime | None = None,
) -> ReferenceImage | None:
    """Return a VERIFIED current reference photo for a resolved person, fetching if needed.

    On a fresh cache hit this makes ZERO network calls. On a miss or a stale row it
    SERP-searches the exact name + year, downloads candidates, runs a Flash identity-
    verification pass, and — ONLY if the best candidate clears
    ``REFERENCE_MIN_CONFIDENCE`` — uploads the bytes and upserts the row. If nothing
    clears the threshold it returns ``None`` and writes NOTHING (the safety guarantee:
    never cache a wrong/low-confidence face).

    Args:
        entity_key: Normalized resolved person name — the unique store lookup key.
        entity_name: The exact resolved person name (drives the SERP + verify prompt).
        entity_kind: Resolved entity kind (normally ``"person"``); stored on the row.
        as_of: ISO story date (YYYY-MM-DD) the likeness must be current for, or None.
        supabase_client: Service-role Supabase client (table reads/writes + storage).
        genai_client: Gemini client for the multimodal verification call.
        current_year: Explicit year to bias the SERP query toward a current photo
            (injected for deterministic tests). Falls back to the year in ``as_of``.
        now: Current time used for staleness (injected for deterministic tests).
            Defaults to ``datetime.now(UTC)``.

    Returns:
        A :class:`ReferenceImage` on a verified hit/fetch, else ``None`` (and nothing
        is written).

    Example:
        >>> ref = await get_or_fetch_entity_reference_image(  # doctest: +SKIP
        ...     entity_key="kevin warsh",
        ...     entity_name="Kevin Warsh",
        ...     entity_kind="person",
        ...     as_of="2026-02-02",
        ...     supabase_client=supabase,
        ...     genai_client=client,
        ... )
        >>> ref.verification_confidence >= 0.7  # doctest: +SKIP
        True
    """
    if not entity_key:
        logger.warning(
            "reference_skip_empty_key",
            entity_name=entity_name,
            fix_suggestion="Caller passed an empty entity_key (entity_kind likely not 'person'); skip the L5 lookup.",
        )
        return None

    now = now or datetime.now(UTC)
    logger.info(
        "reference_lookup_started",
        entity_key=entity_key,
        entity_name=entity_name,
        as_of=as_of,
    )

    # ── 1. Cache hit (fresh) → return without any network call ──
    cached_row = _lookup_cached_row(entity_key, supabase_client)
    if cached_row and _is_row_fresh(cached_row.get("verified_at"), now):
        logger.info("reference_cache_hit", entity_key=entity_key, verified_at=str(cached_row.get("verified_at")))
        return _row_to_reference_image(cached_row)
    if cached_row:
        logger.info(
            "reference_cache_stale",
            entity_key=entity_key,
            verified_at=str(cached_row.get("verified_at")),
        )

    # ── 2. SERP-search the EXACT name + year, download candidates ──
    year = _reference_year(as_of, current_year)
    query = f'"{entity_name}" {year}' if year is not None else f'"{entity_name}"'
    candidates = search_images(query=query, digest_id=f"ref-{entity_key}")[:CANDIDATE_LIMIT]
    logger.info("reference_serp_completed", entity_key=entity_key, query=query, candidate_count=len(candidates))

    # ── 3. Download + Flash identity-verify each candidate; keep the best match ──
    best_bytes: bytes | None = None
    best_confidence: float = 0.0
    best_source_url: str | None = None
    with TemporaryDirectory(prefix="entity-ref-") as tmp_dir:
        refs_dir = Path(tmp_dir)
        for candidate in candidates:
            downloaded = download_candidate(candidate, refs_dir)
            if downloaded is None:
                continue
            verdict = _verify_candidate(downloaded.image_bytes, downloaded.mime_type, entity_name, as_of, genai_client)
            logger.info(
                "reference_candidate_verified",
                entity_key=entity_key,
                candidate_id=candidate.candidate_id,
                is_match=verdict.is_match,
                confidence=round(verdict.confidence, 3),
            )
            if verdict.is_match and verdict.confidence > best_confidence:
                best_bytes = downloaded.image_bytes
                best_confidence = verdict.confidence
                best_source_url = candidate.source_page_url

        # ── 4. Above threshold → upload + upsert + return ──
        if best_bytes is not None and best_confidence >= REFERENCE_MIN_CONFIDENCE:
            object_path, public_url = _upload_reference(supabase_client, entity_key, best_bytes)
            verified_at_iso = now.isoformat()
            row = {
                "entity_key": entity_key,
                "entity_kind": entity_kind or "person",
                "reference_storage_path": object_path,
                "reference_public_url": public_url,
                "source_page_url": best_source_url,
                "verified_at": verified_at_iso,
                "valid_as_of": as_of,
                "verification_confidence": best_confidence,
                "updated_at": verified_at_iso,
            }
            supabase_client.table("entity_reference_images").upsert(row, on_conflict="entity_key").execute()
            logger.info(
                "reference_cached",
                entity_key=entity_key,
                verification_confidence=round(best_confidence, 3),
                reference_public_url=public_url,
            )
            return ReferenceImage(
                entity_key=entity_key,
                entity_kind=entity_kind or "person",
                reference_storage_path=object_path,
                reference_public_url=public_url,
                source_page_url=best_source_url,
                verified_at=verified_at_iso,
                valid_as_of=as_of,
                verification_confidence=best_confidence,
            )

    # ── 5. Nothing cleared the threshold → write NOTHING, return None ──
    logger.warning(
        "reference_rejected",
        entity_key=entity_key,
        entity_name=entity_name,
        best_confidence=round(best_confidence, 3),
        min_confidence=REFERENCE_MIN_CONFIDENCE,
        candidate_count=len(candidates),
        fix_suggestion=(
            "No candidate cleared REFERENCE_MIN_CONFIDENCE; nothing was cached. SP4 falls back to the "
            "unchanged SERP-seed path. Lower REFERENCE_MIN_CONFIDENCE or check the SERP query if this person "
            "should have a findable current photo."
        ),
    )
    return None
