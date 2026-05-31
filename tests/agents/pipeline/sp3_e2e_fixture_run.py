"""⚠ LIVE end-to-end fixture run for Phase 1d SP3 (PAID + IRREVERSIBLE).

This is the ONE real end-to-end story run the SP3 DoD requires. It makes REAL
paid Gemini TTS + image calls and REAL Supabase service-role writes/uploads. It
is **not** a pytest test (no ``test_`` prefix → never collected) and is guarded
behind ``RUN_LIVE_E2E=1`` so a plain ``pytest`` never triggers paid calls. The
hermetic unit suite (``test_ranking`` / ``test_fallback_tree`` / ``test_persist``
/ ``test_orchestrator``) stays free.

WHAT IT DOES (blast-radius: INSERT-only, ONE story)
  1. Builds EXACTLY ONE fixture ``CanonicalStory`` (static, single-source, with a
     real body) labelled ``FIXTURE-SP3-…`` — no live GDELT, isolating SP3.
  2. Runs the real orchestrator: script → verify → TTS → caption → poster →
     persist, with the REAL ``LLMClient`` + real ``GeminiTTSClient`` + real
     poster ``genai.Client`` + real service-role supabase client.
  3. Asserts: a ``stories`` row + a ``digests`` row whose audio URL resolves
     HTTP 200; ``caption_sentences`` rows with ``word_tokens`` ms timings + one
     highlight/sentence; ``poster_url`` resolves; ``story_interests`` rows.
  4. Prints every created row id + every storage object path (auditable/cleanable)
     and the HTTP-200 confirmations.

Run:
    RUN_LIVE_E2E=1 .venv/bin/python tests/agents/pipeline/sp3_e2e_fixture_run.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import httpx

# Reason: ensure the repo root is importable when run as a bare script.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.ingestion.models import CanonicalStory, StoryInterestTag  # noqa: E402
from agents.pipeline.llm_clients import LLMClient  # noqa: E402
from agents.pipeline.orchestrator import orchestrate_story  # noqa: E402
from agents.pipeline.persist import make_story_id  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402
from agents.voice.gemini_tts import GeminiTTSClient  # noqa: E402

logger = get_logger("sp3.e2e_fixture_run")

# Reason: a recognizable fixture body so the verifier grounds + the row is
# cleanable by its FIXTURE-SP3- prefix. Single source, plausible facts.
_FIXTURE_BODY = (
    "The Genoa City Transit Authority announced on Friday that its new automated "
    "light-rail line, the Harbor Loop, carried 41,000 riders on its opening day. "
    "Officials said the 9-mile line connects the downtown core to the eastern "
    "waterfront in under 20 minutes. The authority's director, Mara Velasquez, "
    "said the line cost 380 million dollars to build and was funded by a mix of "
    "city bonds and a federal transit grant. Velasquez said three more stations "
    "are planned for next year if ridership stays above 30,000 a day."
)


def _build_fixture_story(story_id: str) -> CanonicalStory:
    """Build the single static fixture CanonicalStory (no live GDELT)."""
    now = datetime.now(timezone.utc)
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title="FIXTURE-SP3 Genoa City opens the Harbor Loop light-rail",
        canonical_url="https://reuters.com/fixture-sp3/harbor-loop",
        canonical_normalized_url="https://reuters.com/fixture-sp3/harbor-loop",
        canonical_published_utc=now,
        canonical_primary_outlet_domain="reuters.com",
        canonical_primary_outlet_name="Reuters",
        canonical_body_text=_FIXTURE_BODY,
        canonical_representative_external_id="https://reuters.com/fixture-sp3/harbor-loop",
        covering_outlets=["reuters.com", "apnews.com", "bbc.com", "cnn.com"],
        story_outlet_count=4,
        canonical_matched_interest_ids=["int-fixture-transit"],
    )


def _fixture_interest_tags(story_id: str) -> list[StoryInterestTag]:
    """One leaf + one parent story_interests tag for the fixture story.

    NOTE: these interest ids must EXIST in the live ``interests`` table for the
    FK to hold. They are resolved from env (``SP3_E2E_LEAF_INTEREST_ID`` /
    ``SP3_E2E_PARENT_INTEREST_ID``); if unset the run discovers two real interest
    ids from the DB so the FK is satisfied.
    """
    leaf = os.environ.get("SP3_E2E_LEAF_INTEREST_ID", "")
    parent = os.environ.get("SP3_E2E_PARENT_INTEREST_ID", "")
    tags: list[StoryInterestTag] = []
    if leaf:
        tags.append(
            StoryInterestTag(
                story_interest_story_id=story_id,
                story_interest_interest_id=leaf,
                story_interest_match_depth=0,
            )
        )
    if parent:
        tags.append(
            StoryInterestTag(
                story_interest_story_id=story_id,
                story_interest_interest_id=parent,
                story_interest_match_depth=1,
            )
        )
    return tags


def _discover_interest_ids(supabase_client) -> list[StoryInterestTag]:
    """Read up to 2 real interest ids from the live DB (for valid FKs)."""
    response = (
        supabase_client.table("interests")
        .select("interest_id,depth_level")
        .eq("interest_is_active", True)
        .order("depth_level", desc=True)
        .limit(2)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    tags: list[StoryInterestTag] = []
    for depth, row in enumerate(rows):
        tags.append(
            StoryInterestTag(
                story_interest_story_id="",  # filled by the caller
                story_interest_interest_id=str(row["interest_id"]),
                story_interest_match_depth=min(depth, 2),
            )
        )
    return tags


def _http_200(url: str) -> bool:
    """Return True iff a GET on the public URL resolves HTTP 200."""
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
    except httpx.HTTPError as exc:  # noqa: BLE001
        print(f"  HTTP error for {url}: {exc}")
        return False
    print(f"  GET {url} -> {response.status_code} ({len(response.content)} bytes)")
    return response.status_code == 200


async def _run() -> int:
    # Reason: load the project .env so the direct os.environ reads below (and the
    # pydantic Settings inside the clients) see the keys.
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    from supabase import create_client

    supabase_url = os.environ["SUPABASE_URL"]
    service_role_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    gemini_key = os.environ["GEMINI_API_KEY"]

    supabase_client = create_client(supabase_url, service_role_key)

    from google import genai

    poster_client = genai.Client(api_key=gemini_key)
    llm_client = LLMClient()
    tts_client = GeminiTTSClient()

    story_id = make_story_id("FIXTURE-SP3-")
    story = _build_fixture_story(story_id)

    tags = _fixture_interest_tags(story_id)
    if not tags:
        discovered = _discover_interest_ids(supabase_client)
        for tag in discovered:
            tag.story_interest_story_id = story_id
        tags = discovered
    if not tags:
        print(
            "FAIL: no interest ids available for the FK. Seed interests or set "
            "SP3_E2E_LEAF_INTEREST_ID / SP3_E2E_PARENT_INTEREST_ID."
        )
        return 1

    print(f"\n=== SP3 LIVE E2E — story_id={story_id} ===")
    print(f"interest tags: {[t.story_interest_interest_id for t in tags]}")

    result = await orchestrate_story(
        story=story,
        story_interest_tags=tags,
        llm_client=llm_client,
        tts_client=tts_client,
        supabase_client=supabase_client,
        poster_genai_client=poster_client,
        story_id=story_id,
        suggested_questions=[
            "How many riders used the Harbor Loop on opening day?",
            "How much did the line cost to build?",
        ],
    )

    if not result.published:
        print(f"FAIL: story not published (skip_reason={result.skip_reason!r}).")
        return 1

    persist = result.persist_result
    assert persist is not None

    print("\n--- CREATED ROWS (auditable / cleanable) ---")
    print(f"stories.story_id        = {persist.story_id}")
    print(f"digests.digest_id       = {persist.digest_id}")
    for table, ids in persist.created_table_row_ids.items():
        print(f"{table:<24}= {ids}")
    print("\n--- STORAGE OBJECTS ---")
    print(f"digest-audio  : {persist.audio_object_path}")
    print(f"story-posters : {persist.poster_object_path}")
    print(f"audio_url     : {persist.audio_url}")
    print(f"poster_url    : {persist.poster_url}")

    print("\n--- DB READBACK ASSERTIONS ---")
    story_rows = (
        supabase_client.table("stories")
        .select("story_id")
        .eq("story_id", story_id)
        .execute()
    )
    assert getattr(story_rows, "data", None), "stories row not found on readback"
    print(f"  stories row present: {story_rows.data}")

    caption_rows = (
        supabase_client.table("caption_sentences")
        .select("sentence_index,highlight_keyword,word_tokens")
        .eq("caption_story_id", story_id)
        .order("sentence_index")
        .execute()
    )
    captions = getattr(caption_rows, "data", None) or []
    assert captions, "no caption_sentences rows on readback"
    for cap in captions:
        tokens = cap["word_tokens"]
        highlights = [t for t in tokens if t.get("is_highlight")]
        assert len(highlights) == 1, f"sentence {cap['sentence_index']} ≠1 highlight"
        assert all("start_ms" in t and "end_ms" in t for t in tokens), (
            "missing ms timings"
        )
    print(f"  caption_sentences rows: {len(captions)} (each 1 highlight + ms timings)")

    interest_rows = (
        supabase_client.table("story_interests")
        .select("story_interest_interest_id,story_interest_match_depth")
        .eq("story_interest_story_id", story_id)
        .execute()
    )
    assert getattr(interest_rows, "data", None), "no story_interests rows on readback"
    print(f"  story_interests rows: {interest_rows.data}")

    print("\n--- HTTP 200 CHECKS ---")
    audio_ok = _http_200(persist.audio_url)
    poster_ok = _http_200(persist.poster_url) if persist.poster_url else False

    print("\n--- DoD SUMMARY ---")
    print(f"  stories row created       : PASS ({story_id})")
    print(f"  digests row + audio HTTP200: {'PASS' if audio_ok else 'FAIL'}")
    print(f"  caption_sentences lossless : PASS ({len(captions)} rows)")
    print(
        f"  poster_url HTTP200         : {'PASS' if poster_ok else 'FAIL (no poster)'}"
    )
    print(f"  story_interests rows       : PASS ({len(interest_rows.data)})")

    # Audio is mandatory; poster is best-effort (digest still valid without it).
    return 0 if audio_ok else 1


def main() -> None:
    if os.environ.get("RUN_LIVE_E2E") != "1":
        print(
            "Refusing to run: this makes PAID Gemini calls + REAL Supabase writes.\n"
            "Set RUN_LIVE_E2E=1 to run the one-story live fixture e2e."
        )
        sys.exit(2)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
