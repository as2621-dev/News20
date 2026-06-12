"""⚠ LIVE regeneration of EXISTING feed stories — PAID + production writes.

One-shot companion to ``run_live_batch.py`` for the 2026-06-12 voice-personality
+ PROFILE-analytic changes: the produce-once gate (correctly) refuses to re-render
a story that already has a current digest, so a normal batch re-run would leave
today's reels with the old monotone audio and the old MARKET-IMPACT-everywhere
tabs. This script regenerates content for the stories ALREADY in the latest
``daily_feeds`` window, in place:

  per story:
    1. rebuild a ``CanonicalStory`` from the DB (``stories`` + ``detail_chunks``
       body + primary ``story_sources`` row),
    2. re-run scripting → verification (halt = keep the OLD digest, never break
       a working story) → TTS → captions,
    3. upload the new audio to a versioned object path, flip the old digest
       ``digest_is_current=false``, insert the new digest + caption_sentences,
    4. re-run detail enrichment (new segment→kind map incl. subject_profile),
       replace ``story_timeline`` / ``story_analytics`` / ``detail_key_points``
       and update the ``stories`` key-figure columns.

SAFETY:
  * **Dry-run by default** — lists the target stories and exits without paying.
    Set ``RUN_REGEN=1`` to pay.
  * ``MAX_REGEN`` caps how many stories are regenerated (default 0 = all).
  * A story that fails ANY stage keeps its old digest + analytics (the flip to
    the new digest happens only after the new audio is fully persisted).

Run (dry preflight, free):
    .venv/bin/python scripts/regenerate_feed_content.py

Run (paid, full):
    RUN_REGEN=1 .venv/bin/python scripts/regenerate_feed_content.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

# Reason: make the repo root importable when run as a bare script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timezone  # noqa: E402

from agents.ingestion.models import CanonicalStory  # noqa: E402
from agents.pipeline.llm_clients import LLMClient  # noqa: E402
from agents.pipeline.orchestrator import (  # noqa: E402
    build_caption_track,
    render_audio_bytes,
)
from agents.pipeline.persist import AUDIO_BUCKET, upload_to_bucket  # noqa: E402
from agents.pipeline.persist_helpers import (  # noqa: E402
    build_caption_sentence_rows,
    build_detail_key_point_rows,
    build_digest_row,
    build_story_analytics_row,
    build_story_timeline_rows,
    script_speaker_order,
)
from agents.pipeline.stages.detail_enrichment import (  # noqa: E402
    run_detail_enrichment,
)
from agents.pipeline.stages.scripting import run_single_source_scripting  # noqa: E402
from agents.pipeline.stages.verification import (  # noqa: E402
    run_single_source_verification,
)
from agents.shared.exceptions import VerificationHaltError  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402
from agents.voice.gemini_tts import GeminiTTSClient  # noqa: E402

logger = get_logger("scripts.regenerate_feed_content")


def _load_target_story_ids(supabase: Any) -> tuple[str, list[str]]:
    """Find the latest feed_date and the distinct story ids allocated in it."""
    latest_rows = (
        supabase.table("daily_feeds")
        .select("feed_date")
        .order("feed_date", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not latest_rows:
        return "", []
    feed_date = str(latest_rows[0]["feed_date"])
    feed_rows = (
        supabase.table("daily_feeds")
        .select("feed_story_id")
        .eq("feed_date", feed_date)
        .execute()
        .data
        or []
    )
    story_ids = sorted({str(r["feed_story_id"]) for r in feed_rows})
    return feed_date, story_ids


def _rebuild_canonical_story(
    supabase: Any, story_row: dict[str, Any]
) -> CanonicalStory | None:
    """Reconstruct the CanonicalStory for one DB story (body from detail_chunks)."""
    story_id = str(story_row["story_id"])
    chunk_rows = (
        supabase.table("detail_chunks")
        .select("chunk_index,chunk_text")
        .eq("chunk_story_id", story_id)
        .order("chunk_index")
        .execute()
        .data
        or []
    )
    body_text = "\n\n".join(str(r["chunk_text"]) for r in chunk_rows).strip()
    if not body_text:
        logger.warning(
            "regen_story_skipped_no_body",
            story_id=story_id,
            fix_suggestion="detail_chunks empty for this story; cannot ground a re-script.",
        )
        return None

    source_rows = (
        supabase.table("story_sources")
        .select("source_outlet_name,source_article_url,source_published_utc")
        .eq("source_story_id", story_id)
        .execute()
        .data
        or []
    )
    primary = next(
        (r for r in source_rows if r.get("source_article_url")),
        source_rows[0] if source_rows else {},
    )
    article_url = str(
        primary.get("source_article_url") or f"https://news20.app/{story_id}"
    )
    published_raw = str(
        primary.get("source_published_utc")
        or story_row.get("story_first_reported_utc")
        or ""
    )
    try:
        published_utc = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
    except ValueError:
        published_utc = datetime.now(timezone.utc)

    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=str(story_row["story_headline"]),
        canonical_url=article_url,
        canonical_normalized_url=article_url,
        canonical_published_utc=published_utc,
        canonical_primary_outlet_domain=str(
            primary.get("source_outlet_name") or "unknown"
        ),
        canonical_primary_outlet_name=primary.get("source_outlet_name"),
        canonical_social_image_url=None,
        canonical_body_text=body_text,
        canonical_representative_external_id=story_id,
        covering_outlets=[
            str(r["source_outlet_name"])
            for r in source_rows
            if r.get("source_outlet_name")
        ],
        story_outlet_count=int(
            story_row.get("story_outlet_count") or max(len(source_rows), 1)
        ),
        canonical_matched_interest_ids=[],
        member_candidate_ids=[story_id],
    )


async def _regenerate_story(
    supabase: Any,
    llm_client: LLMClient,
    tts_client: GeminiTTSClient,
    story_row: dict[str, Any],
    feed_date: str,
) -> str:
    """Regenerate one story in place. Returns a status string for the summary."""
    story_id = str(story_row["story_id"])
    segment_slug = str(story_row.get("story_segment_slug") or "wildcard")

    story = _rebuild_canonical_story(supabase, story_row)
    if story is None:
        return "skipped_no_body"

    # ── 1. Script + verify (halt = keep the old digest untouched) ──
    script = await run_single_source_scripting(story=story, llm_client=llm_client)
    try:
        await run_single_source_verification(
            script=script, source_story=story, llm_client=llm_client
        )
    except VerificationHaltError as halt:
        logger.warning(
            "regen_story_verification_halt",
            story_id=story_id,
            unsupported_count=halt.unsupported_count,
            fix_suggestion="New script ungrounded; the OLD digest stays current.",
        )
        return "skipped_verification_halt"

    # ── 2. TTS + captions ──
    audio_bytes, audio_duration_ms = await render_audio_bytes(script, tts_client)
    caption_track = build_caption_track(script, audio_duration_ms)

    # ── 3. Upload new audio (versioned path so CDN/clients never see stale bytes) ──
    audio_object_path = f"{story_id}/digest-{feed_date}-regen.mp3"
    audio_url = upload_to_bucket(
        supabase, AUDIO_BUCKET, audio_object_path, audio_bytes, "audio/mpeg"
    )

    # ── 4. Swap digests: old current → false, insert the new current ──
    old_digest_rows = (
        supabase.table("digests")
        .select("digest_id,digest_ambient_poster_url")
        .eq("digest_story_id", story_id)
        .eq("digest_is_current", True)
        .execute()
        .data
        or []
    )
    poster_url = (
        old_digest_rows[0].get("digest_ambient_poster_url") if old_digest_rows else None
    )
    # Reason: the flip happens only AFTER the new audio is uploaded — a failure
    # above leaves the story fully on its old, working digest.
    supabase.table("digests").update({"digest_is_current": False}).eq(
        "digest_story_id", story_id
    ).eq("digest_is_current", True).execute()
    digest_row = build_digest_row(
        digest_story_id=story_id,
        audio_url=audio_url,
        duration_ms=audio_duration_ms,
        poster_url=poster_url,
    )
    inserted = supabase.table("digests").insert(digest_row).execute().data
    digest_id = str(inserted[0]["digest_id"])

    caption_rows = build_caption_sentence_rows(
        digest_id=digest_id,
        story_id=story_id,
        caption_track=caption_track,
        turns_speaker_order=script_speaker_order(script),
    )
    supabase.table("caption_sentences").insert(caption_rows).execute()

    # ── 5. Re-enrich with the new segment→kind map; replace the analytics rows ──
    enrichment = await run_detail_enrichment(
        story=story, script=script, llm_client=llm_client, segment_slug=segment_slug
    )
    supabase.table("story_timeline").delete().eq(
        "timeline_story_id", story_id
    ).execute()
    supabase.table("story_analytics").delete().eq(
        "analytic_story_id", story_id
    ).execute()
    supabase.table("detail_key_points").delete().eq(
        "key_point_story_id", story_id
    ).execute()

    timeline_rows = build_story_timeline_rows(story_id, enrichment.timeline)
    if timeline_rows:
        supabase.table("story_timeline").insert(timeline_rows).execute()
    supabase.table("story_analytics").insert(
        build_story_analytics_row(story_id, enrichment.second_analytic)
    ).execute()
    key_point_rows = build_detail_key_point_rows(story_id, enrichment.key_points)
    if key_point_rows:
        supabase.table("detail_key_points").insert(key_point_rows).execute()
    supabase.table("stories").update(
        {
            "story_key_figure_value": enrichment.key_figure.key_figure_value,
            "story_key_figure_label": enrichment.key_figure.key_figure_label,
        }
    ).eq("story_id", story_id).execute()

    logger.info(
        "regen_story_completed",
        story_id=story_id,
        digest_id=digest_id,
        audio_duration_ms=audio_duration_ms,
        analytic_kind=enrichment.second_analytic.analytic_kind,
    )
    return f"regenerated ({enrichment.second_analytic.analytic_kind})"


async def _run() -> int:
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    paid = os.environ.get("RUN_REGEN") == "1"
    max_regen = int(os.environ.get("MAX_REGEN", "0"))

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    feed_date, story_ids = _load_target_story_ids(supabase)
    if not story_ids:
        print("No daily_feeds rows found — nothing to regenerate.")
        return 1
    if max_regen > 0:
        story_ids = story_ids[:max_regen]

    print(f"\n=== FEED CONTENT REGENERATION — feed_date={feed_date} ===")
    print(f"mode={'PAID' if paid else 'DRY-RUN (free)'}  stories={len(story_ids)}")
    for sid in story_ids:
        print(f"  - {sid}")
    if not paid:
        print("\nDry run only. Set RUN_REGEN=1 to regenerate (paid Gemini calls).")
        return 0

    story_rows = (
        supabase.table("stories")
        .select(
            "story_id,story_headline,story_segment_slug,story_outlet_count,story_first_reported_utc"
        )
        .in_("story_id", story_ids)
        .execute()
        .data
        or []
    )
    rows_by_id = {str(r["story_id"]): r for r in story_rows}

    llm_client = LLMClient()
    tts_client = GeminiTTSClient()
    outcomes: dict[str, str] = {}
    for index, story_id in enumerate(story_ids, start=1):
        story_row = rows_by_id.get(story_id)
        if story_row is None:
            outcomes[story_id] = "skipped_no_story_row"
            continue
        print(
            f"[{index}/{len(story_ids)}] {story_id} — {story_row['story_headline'][:70]}"
        )
        try:
            outcomes[story_id] = await _regenerate_story(
                supabase, llm_client, tts_client, story_row, feed_date
            )
        except Exception as exc:  # noqa: BLE001 — one bad story must not kill the batch
            logger.error(
                "regen_story_failed",
                story_id=story_id,
                error_message=str(exc)[:300],
                fix_suggestion="Story keeps its old digest; inspect and re-run for this id.",
            )
            outcomes[story_id] = f"FAILED: {str(exc)[:120]}"
        print(f"    → {outcomes[story_id]}")

    regenerated = sum(1 for v in outcomes.values() if v.startswith("regenerated"))
    print(f"\nDone: {regenerated}/{len(story_ids)} regenerated.")
    for sid, outcome in outcomes.items():
        if not outcome.startswith("regenerated"):
            print(f"  ⚠ {sid}: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
