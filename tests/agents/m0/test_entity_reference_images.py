"""Tests for fetch + verify + cache of canonical reference photos (phase 0c SP3).

Every external boundary is MOCKED — no network: the SERP search, the candidate
download, the Gemini-Flash verification call, and the Supabase client (table reads/
writes + storage). The four cases mandated by the phase DoD (and CLAUDE.md §6):

  - happy / cache-hit:   a fresh row returns with NO SERP / LLM / upload call.
  - fetch / miss:        verification passes -> bytes uploaded + row upserted.
  - failure / reject:    all candidates below threshold -> None AND nothing written.
  - edge / stale:        a stale row triggers a re-fetch (SERP IS called).

Per CLAUDE.md Rule 9 the reject test ASSERTS no upload + no upsert — it FAILS if a
low-confidence face is ever cached, which is the whole safety guarantee of SP3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

import agents.m0.entity_reference_images as eri
from agents.m0.entity_reference_images import (
    ReferenceImage,
    _VerificationResponse,
    get_or_fetch_entity_reference_image,
)
from agents.m0.poster_models import ImageCandidate

_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


class _FakeStorage:
    """Records upload() calls and returns a deterministic public URL."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    def upload(self, *, path: str, file: bytes, file_options: dict[str, str]) -> None:
        self.uploads.append({"path": path, "file": file, "file_options": file_options})

    def get_public_url(self, path: str) -> str:
        return f"https://cdn.example/entity-reference-images/{path}"


class _FakeTable:
    """Captures select/upsert against the entity_reference_images table."""

    def __init__(self, parent: "_FakeSupabase") -> None:
        self._parent = parent
        self._filter_key: str | None = None

    def select(self, _columns: str) -> "_FakeTable":
        return self

    def eq(self, _column: str, value: str) -> "_FakeTable":
        self._filter_key = value
        return self

    def limit(self, _n: int) -> "_FakeTable":
        return self

    def upsert(self, row: dict[str, Any], *, on_conflict: str | None = None) -> "_FakeTable":
        self._parent.upserts.append({"row": row, "on_conflict": on_conflict})
        return self

    def execute(self) -> MagicMock:
        response = MagicMock()
        # Reason: a select() returns the seeded cached row (if any); an upsert()
        # returns no rows. We seeded rows only for the cache/stale tests.
        response.data = self._parent.existing_rows
        return response


class _FakeSupabase:
    """Minimal Supabase double: one table + storage, recording all writes."""

    def __init__(self, existing_rows: list[dict[str, Any]] | None = None) -> None:
        self.existing_rows = existing_rows or []
        self.upserts: list[dict[str, Any]] = []
        self._storage = _FakeStorage()

    def table(self, _name: str) -> _FakeTable:
        return _FakeTable(self)

    @property
    def storage_double(self) -> _FakeStorage:
        return self._storage

    def storage_from(self) -> _FakeStorage:  # pragma: no cover - convenience
        return self._storage

    # Supabase client exposes ``.storage.from_(bucket)``.
    @property
    def storage(self) -> Any:
        outer = self

        class _StorageNamespace:
            def from_(self, _bucket: str) -> _FakeStorage:
                return outer._storage

        return _StorageNamespace()


def _fresh_row() -> dict[str, Any]:
    """A cached row verified just now (well within the refresh window)."""
    return {
        "reference_id": "row-uuid-1",
        "entity_key": "kevin warsh",
        "entity_kind": "person",
        "reference_storage_path": "kevin warsh/reference.jpg",
        "reference_public_url": "https://cdn.example/entity-reference-images/kevin warsh/reference.jpg",
        "source_page_url": "https://news.example/warsh",
        "verified_at": _NOW.isoformat(),
        "valid_as_of": "2026-06-18",
        "verification_confidence": 0.93,
    }


def _stale_row() -> dict[str, Any]:
    """A cached row verified well beyond REFERENCE_REFRESH_DAYS ago."""
    old = _NOW - timedelta(days=eri.REFERENCE_REFRESH_DAYS + 5)
    row = _fresh_row()
    row["verified_at"] = old.isoformat()
    return row


def _candidate(suffix: str = "1") -> ImageCandidate:
    """A minimal SERP candidate (only fields the fetch path touches)."""
    return ImageCandidate(
        candidate_id=f"ref-kevin warsh-cand-{suffix}",
        title="Kevin Warsh",
        source_page_url=f"https://news.example/warsh-{suffix}",
        full_image_url=f"https://img.example/warsh-{suffix}.jpg",
        thumbnail_url=f"https://img.example/warsh-{suffix}-thumb.jpg",
        width_px=800,
        height_px=1000,
    )


class _FakeDownloaded:
    """Stand-in for DownloadedCandidate (only the attrs the module reads)."""

    def __init__(self, candidate: ImageCandidate, image_bytes: bytes) -> None:
        self.candidate = candidate
        self.image_bytes = image_bytes
        self.mime_type = "image/jpeg"


@pytest.fixture
def patch_boundaries(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch SERP search, candidate download, and the verifier; return the mocks."""
    search_mock = MagicMock(name="search_images", return_value=[_candidate("1"), _candidate("2")])
    download_mock = MagicMock(
        name="download_candidate",
        side_effect=lambda candidate, refs_dir: _FakeDownloaded(
            candidate, b"img-bytes-" + candidate.candidate_id.encode()
        ),
    )
    verify_mock = MagicMock(name="_verify_candidate")
    monkeypatch.setattr(eri, "search_images", search_mock)
    monkeypatch.setattr(eri, "download_candidate", download_mock)
    monkeypatch.setattr(eri, "_verify_candidate", verify_mock)
    return {"search": search_mock, "download": download_mock, "verify": verify_mock}


@pytest.mark.asyncio
async def test_cache_hit_returns_without_any_network_call(
    patch_boundaries: dict[str, MagicMock],
) -> None:
    """WHY: a fresh row is authoritative — re-fetching would waste SERP+LLM+storage.

    A fresh cached row MUST short-circuit: no SERP search, no verification, no upload,
    no upsert. This is the cost guarantee SP4 relies on for repeat entities.
    """
    supabase = _FakeSupabase(existing_rows=[_fresh_row()])
    result = await get_or_fetch_entity_reference_image(
        entity_key="kevin warsh",
        entity_name="Kevin Warsh",
        entity_kind="person",
        as_of="2026-06-18",
        supabase_client=supabase,
        genai_client=MagicMock(),
        now=_NOW,
    )

    assert isinstance(result, ReferenceImage)
    assert result.verification_confidence == 0.93
    patch_boundaries["search"].assert_not_called()
    patch_boundaries["verify"].assert_not_called()
    assert supabase.upserts == []
    assert supabase.storage_double.uploads == []


@pytest.mark.asyncio
async def test_miss_with_passing_verification_uploads_and_upserts(
    patch_boundaries: dict[str, MagicMock],
) -> None:
    """WHY: the populate path must persist the VERIFIED winner so SP4 can read it.

    On a cache miss with a high-confidence match: bytes are uploaded AND a row is
    upserted carrying that confidence; the returned ReferenceImage mirrors it.
    """
    # cand-1 a weak match, cand-2 a strong one -> the strong one must win.
    patch_boundaries["verify"].side_effect = [
        _VerificationResponse(is_match=True, confidence=0.72),
        _VerificationResponse(is_match=True, confidence=0.91),
    ]
    supabase = _FakeSupabase(existing_rows=[])  # miss

    result = await get_or_fetch_entity_reference_image(
        entity_key="kevin warsh",
        entity_name="Kevin Warsh",
        entity_kind="person",
        as_of="2026-02-02",
        supabase_client=supabase,
        genai_client=MagicMock(),
        current_year=2026,
        now=_NOW,
    )

    assert result is not None
    assert result.verification_confidence == 0.91
    # SERP query carries the exact quoted name + year.
    assert patch_boundaries["search"].call_args.kwargs["query"] == '"Kevin Warsh" 2026'
    # Exactly one upload + one upsert, both for the winning (strong) candidate.
    assert len(supabase.storage_double.uploads) == 1
    upload = supabase.storage_double.uploads[0]
    assert upload["path"] == "kevin warsh/reference.jpg"
    assert upload["file"] == b"img-bytes-ref-kevin warsh-cand-2"
    assert len(supabase.upserts) == 1
    upsert = supabase.upserts[0]
    assert upsert["on_conflict"] == "entity_key"
    assert upsert["row"]["entity_key"] == "kevin warsh"
    assert upsert["row"]["verification_confidence"] == 0.91
    assert upsert["row"]["valid_as_of"] == "2026-02-02"


@pytest.mark.asyncio
async def test_all_below_threshold_returns_none_and_writes_nothing(
    patch_boundaries: dict[str, MagicMock],
) -> None:
    """WHY (Rule 9 safety guarantee): a wrong/low-confidence face must NEVER cache.

    Every candidate is below REFERENCE_MIN_CONFIDENCE (or a non-match). The function
    MUST return None and write NOTHING — no upload, no upsert. This test FAILS the
    moment a low-confidence face is cached, which is exactly what SP3 forbids.
    """
    patch_boundaries["verify"].side_effect = [
        _VerificationResponse(is_match=True, confidence=0.55),  # below 0.7
        _VerificationResponse(is_match=False, confidence=0.99),  # high conf but NOT a match
    ]
    supabase = _FakeSupabase(existing_rows=[])

    result = await get_or_fetch_entity_reference_image(
        entity_key="kevin warsh",
        entity_name="Kevin Warsh",
        entity_kind="person",
        as_of="2026-02-02",
        supabase_client=supabase,
        genai_client=MagicMock(),
        current_year=2026,
        now=_NOW,
    )

    assert result is None
    assert supabase.storage_double.uploads == [], "low-confidence face must not be uploaded"
    assert supabase.upserts == [], "low-confidence face must not be persisted"


@pytest.mark.asyncio
async def test_stale_row_triggers_a_refetch(
    patch_boundaries: dict[str, MagicMock],
) -> None:
    """WHY: a person can change role/appearance — a stale row must NOT be trusted.

    Despite a row existing, because it is older than REFERENCE_REFRESH_DAYS the SERP
    search MUST run (re-fetch), proving staleness is honored rather than served.
    """
    patch_boundaries["verify"].side_effect = [
        _VerificationResponse(is_match=True, confidence=0.88),
        _VerificationResponse(is_match=True, confidence=0.80),
    ]
    supabase = _FakeSupabase(existing_rows=[_stale_row()])

    result = await get_or_fetch_entity_reference_image(
        entity_key="kevin warsh",
        entity_name="Kevin Warsh",
        entity_kind="person",
        as_of="2026-02-02",
        supabase_client=supabase,
        genai_client=MagicMock(),
        current_year=2026,
        now=_NOW,
    )

    patch_boundaries["search"].assert_called_once()
    assert result is not None
    assert result.verification_confidence == 0.88
    assert len(supabase.upserts) == 1


@pytest.mark.asyncio
async def test_empty_entity_key_short_circuits_without_lookup() -> None:
    """WHY: a non-person story (empty key) must not invent a SERP query or a row."""
    supabase = _FakeSupabase(existing_rows=[])
    result = await get_or_fetch_entity_reference_image(
        entity_key="",
        entity_name="",
        entity_kind="other",
        as_of=None,
        supabase_client=supabase,
        genai_client=MagicMock(),
        now=_NOW,
    )
    assert result is None
    assert supabase.upserts == []
