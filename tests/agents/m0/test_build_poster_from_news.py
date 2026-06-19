"""Tests for canonical-photo grounding in the poster pipeline (phase 0c SP4).

Every external boundary is MOCKED — no network, no Gemini, no Supabase: the
concept extractor, SERP search, candidate download, scorer, prompt synthesizer,
the canonical-reference store (:func:`get_or_fetch_entity_reference_image`), the
canonical URL byte-fetch (``_fetch``), the image generator
(``generate_from_reference``), and the grade pass.

The DoD this file encodes (CLAUDE.md Rule 9 — tests verify WHY, not just WHAT):

  * canonical PRESENT: when a verified canonical photo exists for the resolved
    person, the bytes handed to ``generate_from_reference`` MUST equal the
    canonical bytes — NOT the SERP winner bytes. (Fails if SERP bytes leak through
    when a trusted face is available — the whole point of the phase.)
  * canonical ABSENT: when none exists, the SERP winner bytes MUST be passed
    exactly as before — no regression.

Both the synchronous path (:func:`build_poster_for_digest`) and the batch path's
selection helper (:func:`resolve_canonical_reference_seed`, also consumed by
:mod:`agents.m0.batch_posters`) are covered.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agents.m0.build_poster_from_news as bpn
from agents.m0.build_poster_from_news import (
    build_poster_for_digest,
    resolve_canonical_reference_seed,
)
from agents.m0.download_candidates import DownloadedCandidate
from agents.m0.entity_reference_images import ReferenceImage
from agents.m0.poster_models import ImageCandidate, ScoredCandidate, StoryConcept

_SERP_WINNER_BYTES = b"serp-winner-image-bytes"
_SERP_WINNER_MIME = "image/png"
_CANONICAL_BYTES = b"verified-canonical-photo-bytes"


def _person_concept() -> StoryConcept:
    """A concept whose resolved primary subject is a named person (L5-eligible)."""
    return StoryConcept(
        image_search_query="kevin warsh fed chair",
        key_subject="Kevin Warsh",
        defining_object_or_action="Federal Reserve podium",
        emotional_valence="serious",
        gist="Trump names Kevin Warsh as Fed chair",
        entity_kind="person",
        entity_name="Kevin Warsh",
        entity_key="kevin warsh",
        entity_as_of="2026-02-02",
    )


def _non_person_concept() -> StoryConcept:
    """A concept that is not a person — the canonical lookup must be skipped."""
    return StoryConcept(
        image_search_query="nvidia gpu launch",
        key_subject="Nvidia",
        defining_object_or_action="GPU",
        emotional_valence="excited",
        gist="Nvidia ships a new GPU",
        entity_kind="company",
        entity_name="Nvidia",
        entity_key="nvidia",
    )


def _reference_image() -> ReferenceImage:
    """A verified canonical reference row pointing at a public URL."""
    return ReferenceImage(
        entity_key="kevin warsh",
        entity_kind="person",
        reference_storage_path="kevin warsh/reference.jpg",
        reference_public_url="https://cdn.example/entity-reference-images/kevin%20warsh/reference.jpg",
        verified_at="2026-06-18T12:00:00+00:00",
        valid_as_of="2026-02-02",
        verification_confidence=0.92,
    )


def _winner_downloaded() -> DownloadedCandidate:
    """The SERP winner the pipeline would otherwise condition on."""
    candidate = ImageCandidate(
        candidate_id="digest-x-cand-0",
        source_page_url="https://news.example/story",
        full_image_url="https://img.example/winner.jpg",
    )
    return DownloadedCandidate(
        candidate=candidate,
        image_bytes=_SERP_WINNER_BYTES,
        mime_type=_SERP_WINNER_MIME,
        local_path=Path("/tmp/winner.jpg"),
    )


def _make_digest() -> bpn.Digest:
    """A minimal M0 digest (the concept is mocked, so content is irrelevant)."""
    from agents.voice.models import DialogueTurn

    return bpn.Digest(
        digest_id="digest-test",
        digest_headline="Trump names Kevin Warsh as Fed chair",
        digest_category="Markets",
        digest_source="Reuters",
        digest_source_url=None,
        turns=[DialogueTurn(speaker="ALEX", text="Warsh gets the nod.")],
    )


@pytest.fixture
def _patched_pipeline(monkeypatch, tmp_path):
    """Stub every boundary of the sync poster pipeline EXCEPT identity grounding.

    Returns a dict with a ``generated`` list capturing the (bytes, mime) that the
    generator was conditioned on, so a test can assert which seed actually won.
    """
    winner = _winner_downloaded()
    monkeypatch.setattr(bpn, "ASSETS_M0_DIR", tmp_path)
    monkeypatch.setattr(bpn, "extract_story_concept", lambda *a, **k: _person_concept())
    monkeypatch.setattr(bpn, "search_images", lambda *a, **k: [winner.candidate])
    monkeypatch.setattr(bpn, "youtube_thumbnail_candidate", lambda *a, **k: None)
    monkeypatch.setattr(bpn, "download_candidate", lambda *a, **k: winner)
    monkeypatch.setattr(
        bpn,
        "score_candidates",
        lambda downloaded, concept, client: [
            ScoredCandidate(candidate=winner.candidate, weighted_total=9.0)
        ],
    )
    monkeypatch.setattr(bpn, "select_winner", lambda scored: scored[0])
    monkeypatch.setattr(bpn, "synthesize_prompt", lambda *a, **k: "a recast poster prompt")
    monkeypatch.setattr(bpn, "grade_and_brand", lambda raw, accent: b"graded-webp")

    generated: list[tuple[bytes, str]] = []

    def _fake_generate(client, prompt, image_bytes, mime_type):
        generated.append((image_bytes, mime_type))
        response = MagicMock()
        return response

    monkeypatch.setattr(bpn, "generate_from_reference", _fake_generate)
    monkeypatch.setattr(bpn, "_extract_image_bytes", lambda response: (b"raw-image", "image/png"))
    return {"generated": generated}


def test_canonical_present_conditions_on_canonical_bytes_not_serp(_patched_pipeline, monkeypatch):
    """A verified canonical photo overrides the SERP winner as the conditioning seed.

    Rule 9: this FAILS if the SERP winner bytes are used when a trusted face exists
    — which is the exact stale-prior bug phase 0c eliminates.
    """
    monkeypatch.setattr(
        bpn,
        "get_or_fetch_entity_reference_image",
        lambda *a, **k: _coroutine_returning(_reference_image()),
    )
    monkeypatch.setattr(bpn, "_fetch", lambda url: _CANONICAL_BYTES)

    supabase = MagicMock()
    report = build_poster_for_digest(_make_digest(), MagicMock(), supabase_client=supabase)

    assert report.poster_path is not None
    generated = _patched_pipeline["generated"]
    assert len(generated) == 1
    conditioned_bytes, conditioned_mime = generated[0]
    assert conditioned_bytes == _CANONICAL_BYTES
    assert conditioned_bytes != _SERP_WINNER_BYTES
    assert conditioned_mime == "image/jpeg"


def test_canonical_absent_passes_serp_winner_bytes_unchanged(_patched_pipeline, monkeypatch):
    """No verified photo → the SERP winner bytes are conditioned on, exactly as today."""
    monkeypatch.setattr(
        bpn,
        "get_or_fetch_entity_reference_image",
        lambda *a, **k: _coroutine_returning(None),
    )
    # _fetch must NOT be needed; make it explode if called so the fallback is proven pure.
    monkeypatch.setattr(bpn, "_fetch", _exploding_fetch)

    supabase = MagicMock()
    report = build_poster_for_digest(_make_digest(), MagicMock(), supabase_client=supabase)

    assert report.poster_path is not None
    generated = _patched_pipeline["generated"]
    assert len(generated) == 1
    conditioned_bytes, conditioned_mime = generated[0]
    assert conditioned_bytes == _SERP_WINNER_BYTES
    assert conditioned_mime == _SERP_WINNER_MIME


def test_no_supabase_client_keeps_serp_path_unchanged(_patched_pipeline, monkeypatch):
    """The default production wiring (no store client) never touches the L5 lookup."""

    def _must_not_be_called(*_a, **_k):  # pragma: no cover - asserts absence
        raise AssertionError("L5 lookup must not run without a supabase_client")

    monkeypatch.setattr(bpn, "get_or_fetch_entity_reference_image", _must_not_be_called)

    report = build_poster_for_digest(_make_digest(), MagicMock())

    assert report.poster_path is not None
    conditioned_bytes, _mime = _patched_pipeline["generated"][0]
    assert conditioned_bytes == _SERP_WINNER_BYTES


# ── Shared selection helper (also the batch path's seam) ──────────────────────


def test_resolve_seed_returns_canonical_bytes_for_verified_person(monkeypatch):
    """The shared helper returns canonical (bytes, jpeg) when a verified photo exists."""
    monkeypatch.setattr(
        bpn,
        "get_or_fetch_entity_reference_image",
        lambda *a, **k: _coroutine_returning(_reference_image()),
    )
    monkeypatch.setattr(bpn, "_fetch", lambda url: _CANONICAL_BYTES)

    result = resolve_canonical_reference_seed(_person_concept(), MagicMock(), MagicMock())

    assert result == (_CANONICAL_BYTES, "image/jpeg")


def test_resolve_seed_returns_none_when_no_verified_photo(monkeypatch):
    """No verified photo → helper returns None so the caller keeps the SERP seed."""
    monkeypatch.setattr(
        bpn,
        "get_or_fetch_entity_reference_image",
        lambda *a, **k: _coroutine_returning(None),
    )

    result = resolve_canonical_reference_seed(_person_concept(), MagicMock(), MagicMock())

    assert result is None


def test_resolve_seed_skips_non_person_concept(monkeypatch):
    """A non-person subject never triggers the L5 lookup (returns None immediately)."""

    def _must_not_be_called(*_a, **_k):  # pragma: no cover - asserts absence
        raise AssertionError("L5 lookup must not run for a non-person subject")

    monkeypatch.setattr(bpn, "get_or_fetch_entity_reference_image", _must_not_be_called)

    result = resolve_canonical_reference_seed(_non_person_concept(), MagicMock(), MagicMock())

    assert result is None


def test_resolve_seed_returns_none_without_supabase_client():
    """No store client → None (the SERP path stays byte-for-byte unchanged)."""
    result = resolve_canonical_reference_seed(_person_concept(), None, MagicMock())
    assert result is None


def test_resolve_seed_falls_back_when_url_fetch_fails(monkeypatch):
    """A verified row whose URL fails to download falls back to the SERP seed (None)."""
    monkeypatch.setattr(
        bpn,
        "get_or_fetch_entity_reference_image",
        lambda *a, **k: _coroutine_returning(_reference_image()),
    )
    monkeypatch.setattr(bpn, "_fetch", lambda url: None)

    result = resolve_canonical_reference_seed(_person_concept(), MagicMock(), MagicMock())

    assert result is None


# ── tiny async helpers (the SP3 function is awaited via the sync bridge) ──────


async def _coroutine_returning(value):
    """Return ``value`` from a coroutine so the sync bridge can await it."""
    return value


def _exploding_fetch(url):  # pragma: no cover - only asserts it is never called
    raise AssertionError("_fetch must not be called when no canonical photo exists")
