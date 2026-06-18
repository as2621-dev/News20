"""Batch-API poster generation for Nano Banana Pro (Gemini 3 Pro Image).

The synchronous poster path (:mod:`agents.m0.build_poster_from_news`) makes one
paid ``generate_content`` call per reel. This module runs the SAME cheap
concept -> SERP -> score -> prompt prep for many reels, then submits ALL the
image-conditioned generations as ONE async **Gemini Batch** job (priced ~50%
below interactive) and returns the raw image bytes per reel. Only the expensive
image call moves to batch; the prep is byte-for-byte the proven path.

Used by ``scripts/fill_batch_posters.py`` to fill posters AFTER a poster-less
produce run (``scripts/run_live_batch.py`` with ``POSTER_MODE=batch``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from agents.m0.digests_input import Digest
from agents.m0.download_candidates import DownloadedCandidate, download_candidate
from agents.m0.generate_posters import (
    GEMINI_IMAGE_MODEL,
    POSTER_ASPECT_RATIO,
    POSTER_IMAGE_SIZE,
    _extract_image_bytes,
)
from agents.m0.image_scorer import score_candidates, select_winner
from agents.m0.poster_models import CANDIDATE_LIMIT, DEFAULT_ACCENT_HEX
from agents.m0.reference_prompt_synthesizer import synthesize_prompt
from agents.m0.serper_image_search import search_images, youtube_thumbnail_candidate
from agents.m0.story_concept import extract_story_concept
from agents.shared.logger import get_logger

logger = get_logger("m0.batch_posters")

# Terminal Gemini batch job states (google.genai JobState names).
_TERMINAL_STATES = frozenset(
    {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    }
)

# Reason: downscale the SERP reference image before batching so the INLINE batch
# payload stays small (each request carries its seed image). The reference only
# guides composition/style — full resolution adds payload, not quality.
_REFERENCE_MAX_SIDE_PX: int = 1280

ASSETS_M0_DIR: Path = Path(__file__).resolve().parents[2] / "assets" / "m0"


@dataclass
class PreparedPoster:
    """A reel's poster generation request, prepped but not yet generated.

    Attributes:
        digest_id: The reel's id (the persisted ``stories.story_id``).
        synthesized_prompt: The recast image-conditioned poster prompt.
        reference_image_bytes: The (downscaled) winning SERP seed image bytes.
        reference_mime_type: Mime type of ``reference_image_bytes``.
        accent_hex: The brand accent used at grade time.
        winner_candidate_id: The chosen candidate id (audit).
    """

    digest_id: str
    synthesized_prompt: str
    reference_image_bytes: bytes
    reference_mime_type: str
    accent_hex: str
    winner_candidate_id: str


def _summary_from_digest(digest: Digest) -> str:
    """Join the dialogue turns into a compact plain-text summary."""
    return " ".join(turn.text for turn in digest.turns)


def _downscale_reference(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Downscale a reference image so the inline batch payload stays small.

    Returns the original bytes unchanged if decoding fails or it is already small.
    """
    try:
        image = Image.open(BytesIO(image_bytes))
        image.load()
        if max(image.size) <= _REFERENCE_MAX_SIDE_PX:
            return image_bytes, mime_type
        ratio = _REFERENCE_MAX_SIDE_PX / max(image.size)
        new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        resized = image.convert("RGB").resize(new_size, Image.LANCZOS)
        out = BytesIO()
        resized.save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 — a bad decode just keeps the original bytes
        return image_bytes, mime_type


def prepare_poster_generation(
    digest: Digest,
    client: genai.Client,
    *,
    accent_hex: str = DEFAULT_ACCENT_HEX,
) -> PreparedPoster | None:
    """Run the cheap poster prep (concept -> SERP -> score -> prompt), no image call.

    Mirrors steps (0)-(8) of :func:`agents.m0.build_poster_from_news.build_poster_for_digest`
    but STOPS before the paid ``generate_from_reference`` call, returning the
    synthesized prompt + chosen reference image so the caller can batch the
    generation. Returns ``None`` (logged) when no usable candidate is found, so
    the caller can decide to skip or fall back.

    Args:
        digest: The reel (headline + dialogue turns seed the concept).
        client: Initialized google-genai client (LLM + scoring use it).
        accent_hex: Brand accent passed to the prompt synthesizer + grade.

    Returns:
        A :class:`PreparedPoster`, or ``None`` when prep could not produce a seed.
    """
    headline = digest.digest_headline
    summary = _summary_from_digest(digest)
    refs_dir = ASSETS_M0_DIR / digest.digest_id / "refs"

    concept = extract_story_concept(headline, summary, client)
    candidates = search_images(concept.image_search_query, digest.digest_id)[
        :CANDIDATE_LIMIT
    ]
    youtube_candidate = youtube_thumbnail_candidate(
        digest.digest_source_url or "", digest.digest_id
    )
    if youtube_candidate is not None:
        candidates = [youtube_candidate, *candidates]
    if not candidates:
        logger.error(
            "batch_poster_prep_no_candidates",
            digest_id=digest.digest_id,
            fix_suggestion="Broaden the concept query or relax the size gate.",
        )
        return None

    downloaded: list[DownloadedCandidate] = [
        d for c in candidates if (d := download_candidate(c, refs_dir)) is not None
    ]
    if not downloaded:
        logger.error(
            "batch_poster_prep_no_downloads",
            digest_id=digest.digest_id,
            fix_suggestion="Candidate hosts blocked fetch; try other results.",
        )
        return None

    scored = score_candidates(downloaded, concept, client)
    winner = select_winner(scored)
    if winner is None:
        logger.error("batch_poster_prep_no_winner", digest_id=digest.digest_id)
        return None
    winner_downloaded = next(
        d
        for d in downloaded
        if d.candidate.candidate_id == winner.candidate.candidate_id
    )

    synthesized_prompt = synthesize_prompt(
        winner_downloaded, concept, accent_hex, client
    )
    reference_bytes, reference_mime = _downscale_reference(
        winner_downloaded.image_bytes, winner_downloaded.mime_type
    )
    logger.info(
        "batch_poster_prep_ready",
        digest_id=digest.digest_id,
        winner_candidate_id=winner.candidate.candidate_id,
        reference_bytes=len(reference_bytes),
    )
    return PreparedPoster(
        digest_id=digest.digest_id,
        synthesized_prompt=synthesized_prompt,
        reference_image_bytes=reference_bytes,
        reference_mime_type=reference_mime,
        accent_hex=accent_hex,
        winner_candidate_id=winner.candidate.candidate_id,
    )


def _image_config() -> types.GenerateContentConfig:
    """The SAME config the synchronous Nano Banana Pro poster call uses."""
    return types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(
            aspect_ratio=POSTER_ASPECT_RATIO, image_size=POSTER_IMAGE_SIZE
        ),
    )


def _chunks(seq: list, size: int):
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def generate_posters_batch(
    client: genai.Client,
    prepared: list[PreparedPoster],
    *,
    chunk_size: int = 6,
    poll_interval_s: int = 20,
    deadline_s: int = 2700,
) -> dict[str, bytes]:
    """Generate all prepared posters via async Gemini Batch jobs.

    Submits the prepared requests as inline batch jobs (chunked so each job's
    payload stays under the inline size limit), then polls every job to a
    terminal state and collects the returned image bytes. All jobs are submitted
    up front so they run concurrently server-side.

    Args:
        client: Initialized google-genai client.
        prepared: The prepped poster requests (image-conditioned).
        chunk_size: Requests per inline batch job (bounds payload size).
        poll_interval_s: Seconds between status polls.
        deadline_s: Give up waiting after this many seconds (caller falls back).

    Returns:
        ``{digest_id: raw_image_bytes}`` for every request that returned an image.
        A digest_id absent from the map either failed or did not finish in time —
        the caller fills those synchronously.
    """
    if not prepared:
        return {}

    jobs: list[tuple[str, list[str]]] = []
    for chunk in _chunks(prepared, chunk_size):
        requests = [
            types.InlinedRequest(
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(
                                data=item.reference_image_bytes,
                                mime_type=item.reference_mime_type,
                            ),
                            types.Part(text=item.synthesized_prompt),
                        ],
                    )
                ],
                config=_image_config(),
                metadata={"key": item.digest_id},
            )
            for item in chunk
        ]
        job = client.batches.create(
            model=GEMINI_IMAGE_MODEL,
            src=requests,
            config=types.CreateBatchJobConfig(display_name="news20-posters"),
        )
        jobs.append((job.name, [item.digest_id for item in chunk]))
        logger.info("batch_poster_job_submitted", job=job.name, count=len(chunk))

    results: dict[str, bytes] = {}
    pending: dict[str, list[str]] = dict(jobs)
    deadline = time.time() + deadline_s
    while pending and time.time() < deadline:
        time.sleep(poll_interval_s)
        for job_name in list(pending):
            try:
                job = client.batches.get(name=job_name)
            except Exception as exc:  # noqa: BLE001 — transient poll error, retry next loop
                logger.warning("batch_poster_poll_error", job=job_name, error=str(exc))
                continue
            state = getattr(job.state, "name", str(job.state))
            if state not in _TERMINAL_STATES:
                continue
            digest_ids = pending.pop(job_name)
            if state != "JOB_STATE_SUCCEEDED":
                logger.error(
                    "batch_poster_job_failed",
                    job=job_name,
                    state=state,
                    error=str(getattr(job, "error", None)),
                    fix_suggestion="Caller fills these reels synchronously.",
                )
                continue
            dest = getattr(job, "dest", None)
            inlined = getattr(dest, "inlined_responses", None) if dest else None
            if not inlined:
                logger.error("batch_poster_job_no_responses", job=job_name)
                continue
            for index, response_item in enumerate(inlined):
                if index >= len(digest_ids):
                    break
                digest_id = digest_ids[index]
                response = getattr(response_item, "response", None)
                if response is None:
                    logger.error(
                        "batch_poster_item_error",
                        job=job_name,
                        digest_id=digest_id,
                        error=str(getattr(response_item, "error", None)),
                    )
                    continue
                image_bytes, _mime = _extract_image_bytes(response)
                if image_bytes:
                    results[digest_id] = image_bytes
                else:
                    logger.error("batch_poster_item_no_image", digest_id=digest_id)
            logger.info(
                "batch_poster_job_collected",
                job=job_name,
                got=sum(1 for d in digest_ids if d in results),
                of=len(digest_ids),
            )

    if pending:
        logger.error(
            "batch_poster_jobs_unfinished",
            remaining_jobs=list(pending),
            fix_suggestion="Deadline hit; caller fills the missing reels synchronously.",
        )
    return results
