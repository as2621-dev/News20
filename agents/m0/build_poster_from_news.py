"""Orchestrate the SERP-seeded poster pipeline for ONE digest.

search (Serper) -> gate -> download top N -> score (6 criteria) -> select ->
recast prompt (Gemini Flash) -> image-conditioned generate (Nano Banana Pro) ->
grade -> write ``assets/m0/<digest>/poster.png`` + ``selection-report.json``.

Each story is independent; a failure is recorded in the report and surfaced
(Rule 12), it does not raise unless image generation itself errors.
"""

from __future__ import annotations

import json
from pathlib import Path

from google import genai

from agents.m0.digests_input import Digest
from agents.m0.download_candidates import DownloadedCandidate, download_candidate
from agents.m0.generate_posters import _extract_image_bytes, generate_from_reference
from agents.m0.grade_and_brand import grade_and_brand
from agents.m0.image_scorer import score_candidates, select_winner
from agents.m0.poster_models import (
    CANDIDATE_LIMIT,
    DEFAULT_ACCENT_HEX,
    SEGMENT_ACCENT_BY_DIGEST,
    ScoredCandidate,
    SelectionReport,
)
from agents.m0.reference_prompt_synthesizer import synthesize_prompt
from agents.m0.serper_image_search import search_images
from agents.m0.story_concept import extract_story_concept
from agents.shared.logger import get_logger

logger = get_logger("m0.build_poster_from_news")

ASSETS_M0_DIR: Path = Path(__file__).resolve().parents[2] / "assets" / "m0"


def _summary_from_digest(digest: Digest) -> str:
    """Join the dialogue turns into a compact plain-text summary."""
    return " ".join(turn.text for turn in digest.turns)


def build_poster_for_digest(digest: Digest, client: genai.Client) -> SelectionReport:
    """Run the full seed→generate→grade pipeline for one digest.

    Args:
        digest: The story (headline + dialogue turns).
        client: Initialized google-genai client.

    Returns:
        A SelectionReport (also written to disk as selection-report.json).
    """
    headline = digest.digest_headline
    summary = _summary_from_digest(digest)
    accent_hex = SEGMENT_ACCENT_BY_DIGEST.get(digest.digest_id, DEFAULT_ACCENT_HEX)
    output_dir = ASSETS_M0_DIR / digest.digest_id
    refs_dir = output_dir / "refs"

    report = SelectionReport(
        digest_id=digest.digest_id, headline=headline, accent_hex=accent_hex,
        refined_query="", candidate_count=0,
    )

    # (0) concept-first (poster-pipeline §4): drives query, scoring, and synthesis.
    concept = extract_story_concept(headline, summary, client)
    report.story_concept = concept.model_dump()

    # (1) search on the concept query  (2) gate is inside search_images
    report.refined_query = concept.image_search_query
    candidates = search_images(concept.image_search_query, digest.digest_id)[:CANDIDATE_LIMIT]
    report.candidate_count = len(candidates)
    if not candidates:
        report.notes = "no candidates returned from SERP after gating"
        logger.error("poster_pipeline_no_candidates", digest_id=digest.digest_id,
                     fix_suggestion="Broaden the query or relax the size gate.")
        _write_report(report, output_dir)
        return report

    # (4) download
    downloaded: list[DownloadedCandidate] = [
        d for c in candidates if (d := download_candidate(c, refs_dir)) is not None
    ]
    if not downloaded:
        report.notes = "all candidate downloads failed"
        logger.error("poster_pipeline_no_downloads", digest_id=digest.digest_id,
                     fix_suggestion="Candidate hosts blocked fetch; try other results or thumbnails.")
        _write_report(report, output_dir)
        return report

    # (5) score (concept-aware)  (6) select
    scored: list[ScoredCandidate] = score_candidates(downloaded, concept, client)
    report.scored = scored
    winner = select_winner(scored)
    if winner is None:
        report.notes = "scoring produced no winner"
        _write_report(report, output_dir)
        return report
    report.winner_candidate_id = winner.candidate.candidate_id
    winner_downloaded = next(d for d in downloaded if d.candidate.candidate_id == winner.candidate.candidate_id)

    # (7) recast prompt  (8) image-conditioned generate  (9) grade
    synthesized_prompt = synthesize_prompt(winner_downloaded, concept, accent_hex, client)
    report.synthesized_prompt = synthesized_prompt

    response = generate_from_reference(
        client, synthesized_prompt, winner_downloaded.image_bytes, winner_downloaded.mime_type
    )
    raw_bytes, _mime = _extract_image_bytes(response)
    if not raw_bytes:
        report.notes = "Nano Banana Pro returned no image part (safety filter or empty)"
        logger.error("poster_pipeline_generation_empty", digest_id=digest.digest_id,
                     fix_suggestion="Rephrase the synthesized prompt to be less literal/sensitive.")
        _write_report(report, output_dir)
        return report

    graded_png = grade_and_brand(raw_bytes, accent_hex)
    output_dir.mkdir(parents=True, exist_ok=True)
    poster_path = output_dir / "poster.png"
    poster_path.write_bytes(graded_png)
    report.poster_path = str(poster_path)

    logger.info(
        "poster_pipeline_completed", digest_id=digest.digest_id,
        winner_candidate_id=report.winner_candidate_id, poster_path=str(poster_path),
    )
    _write_report(report, output_dir)
    return report


def _write_report(report: SelectionReport, output_dir: Path) -> None:
    """Persist the selection report as JSON for auditability."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "selection-report.json").write_text(
        json.dumps(report.model_dump(), indent=2), encoding="utf-8"
    )
