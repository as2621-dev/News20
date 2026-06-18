"""Produce ash's followed-source reels (3 YouTube + 1 X) and place them in today's feed.

WHY a dedicated script (not ``run_live_batch``): today's feed already exists and the
batch is produce-once — a full re-run would gate out ash's already-produced topic
reels and lose them from assembly. This script instead:

  1. Ingests ash's followed YouTube + X sources via ``run_source_ingestion``
     (stamping ``content_sources.last_fetched_at`` so cadence engages next time).
  2. Produces up to ``MAX_YT`` YouTube + ``MAX_X`` X reels via ``orchestrate_story``
     — the proven source path: the video thumbnail / tweet image becomes the poster,
     so NO Nano Banana image generation is paid.
  3. Surgically rebuilds ash's ``daily_feeds`` for today: keeps his existing breaking
     + topic reels (capped to his per-category "Build your 30" budgets), inserts the
     new source reels at their ``youtube``/``x`` allocation positions as
     ``feed_slot_kind='source'``, and totals 30. The prior rows are backed up to a
     JSON file first (reversible: restore the backup, or re-run the daily batch).

PAID (TTS + a few text-LLM calls + xAI for X). Dry-run by default — it ingests +
selects + prints the plan WITHOUT producing or writing. Set ``RUN_SOURCE_REELS=1``
to produce + rewrite the feed.

    .venv/bin/python scripts/produce_source_reels.py                 # dry-run
    RUN_SOURCE_REELS=1 .venv/bin/python scripts/produce_source_reels.py  # paid
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.ingestion.models import CanonicalStory  # noqa: E402
from agents.ingestion.source_pipeline import run_source_ingestion  # noqa: E402
from agents.pipeline.categories import category_for_slug  # noqa: E402
from agents.pipeline.daily_batch import _load_has_current_digest  # noqa: E402
from agents.pipeline.orchestrator import orchestrate_story  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402
from scripts.run_live_batch import _load_followed_sources_by_user  # noqa: E402

logger = get_logger("scripts.produce_source_reels")

ASH_UID = "b316800d-d67c-4e38-898b-ba67ca3a171d"
MAX_YT = int(os.environ.get("MAX_YT", "3"))
MAX_X = int(os.environ.get("MAX_X", "1"))

# Reason: source reels are placed by the source axis, not a Score — give them a flat
# qualifying score so they sort cleanly at the head of their source slot block.
_SOURCE_FEED_SCORE = 1.0


def _category_of_existing_row(
    row: dict[str, Any], slug_by_interest_id: dict[str, str]
) -> str:
    """Map an existing daily_feeds row to its allocation category.

    A breaking row keeps ``breaking``; an interest row resolves its
    ``feed_matched_interest_id`` → interest slug → screen category (the same
    ``category_for_slug`` the allocator uses).
    """
    if row.get("feed_slot_kind") == "breaking":
        return "breaking"
    interest_id = row.get("feed_matched_interest_id")
    slug = slug_by_interest_id.get(str(interest_id), "") if interest_id else ""
    return category_for_slug(slug)


async def _main() -> int:
    from dotenv import load_dotenv
    from google import genai
    from supabase import create_client

    from agents.pipeline.llm_clients import LLMClient
    from agents.voice.gemini_tts import GeminiTTSClient

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    paid = os.environ.get("RUN_SOURCE_REELS") == "1"
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    target = date.today()

    print(f"\n=== SOURCE REELS for ash ({ASH_UID}) — feed_date={target.isoformat()} ===")
    print(f"mode={'PAID' if paid else 'DRY-RUN (free)'}  want={MAX_YT} youtube + {MAX_X} x")

    # ── 1. Ingest ash's followed YouTube/X sources ────────────────────────────
    sources_by_user = _load_followed_sources_by_user(supabase, [ASH_UID])
    sources = sources_by_user.get(ASH_UID, [])
    yt_count = sum(1 for s in sources if s.content_source_type == "youtube_channel")
    x_count = sum(1 for s in sources if s.content_source_type == "x_account")
    print(f"\nash follows {yt_count} youtube + {x_count} x in-scope sources")
    if not sources:
        print("FAIL: ash has no in-scope (youtube/x) follows — nothing to ingest.")
        return 1

    # Reason: a prior run stamps last_fetched_at, so the 6h cadence would skip every
    # source on a re-run. FORCE_REINGEST=1 clears it for ash's in-scope sources first
    # so a manual re-run re-fetches (and gets fresh produce-until-target attempts).
    if os.environ.get("FORCE_REINGEST") == "1":
        source_ids = [s.source_id for s in sources]
        supabase.table("content_sources").update({"last_fetched_at": None}).in_(
            "source_id", source_ids
        ).execute()
        for s in sources:
            s.last_fetched_at = None
        print(f"FORCE_REINGEST: cleared last_fetched_at for {len(source_ids)} sources")

    async def _mark_polled(source_id: str, now: datetime) -> None:
        try:
            supabase.table("content_sources").update(
                {"last_fetched_at": now.isoformat()}
            ).eq("source_id", source_id).execute()
        except Exception as exc:  # noqa: BLE001 — write-back is best-effort
            logger.warning("mark_source_polled_failed", source_id=source_id, error=str(exc))

    # Reason: the Playwright tweet-screenshot renderer can't load the twitter-widget
    # embed in this environment (30s timeout per tweet) — a pre-existing infra gap,
    # not the feed wiring. Inject a no-op renderer so X ingestion is fast; the X
    # reel then falls back to a generated poster (flagged) instead of a real
    # tweet image. Cap max_posts so a prolific account (elonmusk) isn't unbounded.
    from agents.ingestion.adapters.x_account import XAccountAdapter

    async def _no_screenshot(_tweet_url: str) -> None:
        return None

    x_adapter = XAccountAdapter(screenshot_renderer=_no_screenshot, max_posts=5)

    result = await run_source_ingestion(
        ASH_UID,
        sources,
        x_adapter=x_adapter,
        mark_source_polled=_mark_polled if paid else None,
    )
    promoted = result.promoted_stories
    print(
        f"ingest: polled={len(result.polled_source_ids)} "
        f"failed={len(result.failed_source_ids)} fetched={result.items_fetched} "
        f"dropped_dedup={result.items_dropped_dedup} promoted={len(promoted)}"
    )

    # FULL per-domain candidate pools (NOT capped) — the single-source verification
    # gate halts many source reels (over-claims vs messy YouTube auto-captions /
    # short tweets), so we produce-until-target over the whole pool rather than the
    # first N. Order is the promotion order (best/most-substantive first).
    yt_pool = [
        p.story
        for p in promoted
        if (p.story.canonical_primary_outlet_domain or "").lower() == "youtube.com"
    ]
    x_pool = [
        p.story
        for p in promoted
        if (p.story.canonical_primary_outlet_domain or "").lower() == "x.com"
    ]

    print(f"\ncandidate pool: {len(yt_pool)} youtube + {len(x_pool)} x (want {MAX_YT}+{MAX_X}):")
    for story in yt_pool[:8] + x_pool[:4]:
        print(
            f"  [{story.canonical_primary_outlet_domain}] {story.canonical_story_id}  "
            f"{story.canonical_title[:70]!r}"
        )

    if not yt_pool and not x_pool:
        print("\nFAIL: no fresh substantive source items found this run (throttle / nothing new).")
        return 1

    if not paid:
        print("\nDRY-RUN complete — no paid calls, feed unchanged. Re-run with RUN_SOURCE_REELS=1.\n")
        return 0

    # ── 2. Produce until target (thumbnail poster, no Nano Banana) ─────────────
    llm_client = LLMClient()
    tts_client = GeminiTTSClient()
    poster_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    async def _produce_until(pool: list[CanonicalStory], want: int) -> list[str]:
        """Produce stories from ``pool`` until ``want`` publish; return persisted ids.

        Skips a story that already has a current digest (an earlier run produced it)
        and COUNTS it toward the target — so a re-run never re-pays for a reel that
        already exists, yet still fills the slot with its persisted id.
        """
        produced_ids: list[str] = []
        for story in pool:
            if len(produced_ids) >= want:
                break
            persisted_id = f"sp3-{story.canonical_story_id}"[:255]
            if _load_has_current_digest(supabase, [persisted_id]).get(persisted_id):
                produced_ids.append(persisted_id)
                print(f"  reuse {story.canonical_story_id} → {persisted_id} (already produced)")
                continue
            try:
                orch = await orchestrate_story(
                    story, [], llm_client, tts_client, supabase,
                    poster_genai_client=poster_client,
                )
            except Exception as exc:  # noqa: BLE001 — one bad reel must not abort the batch
                print(f"  PRODUCE FAILED {story.canonical_story_id}: {type(exc).__name__}: {exc}")
                continue
            if orch.published and orch.persist_result is not None:
                produced_ids.append(orch.persist_result.story_id)
                print(f"  produced {story.canonical_story_id} → {orch.persist_result.story_id}")
            else:
                print(f"  skipped {story.canonical_story_id} ({orch.skip_reason})")
        return produced_ids

    produced_yt = await _produce_until(yt_pool, MAX_YT)
    produced_x = await _produce_until(x_pool, MAX_X)

    print(f"\nproduced {len(produced_yt)} youtube + {len(produced_x)} x reels")
    if not produced_yt and not produced_x:
        print("FAIL: nothing produced — feed left unchanged.")
        return 1

    # ── 3. Surgical feed rebuild (back up first, then honour ash's allocation) ──
    return _rebuild_feed(supabase, target, produced_yt, produced_x)


def _rebuild_feed(
    supabase: Any,
    target: date,
    produced_yt: list[str],
    produced_x: list[str],
) -> int:
    """Rebuild ash's daily_feeds for ``target`` with the source reels in their slots.

    Keeps his existing breaking + topic reels (capped to each category's allocation
    budget), inserts the produced source reels at the youtube/x allocation positions
    as ``feed_slot_kind='source'``, backfills any shortfall from trimmed topic reels,
    and totals up to 30. Backs up the prior rows to a JSON file before rewriting.
    """
    feed_date_iso = target.isoformat()
    existing = (
        supabase.table("daily_feeds")
        .select("feed_story_id,feed_score,feed_matched_interest_id,feed_slot_kind,feed_position")
        .eq("feed_user_id", ASH_UID)
        .eq("feed_date", feed_date_iso)
        .order("feed_position")
        .execute()
        .data
        or []
    )
    backup_path = f"/tmp/ash_feed_backup_{feed_date_iso}.json"
    with open(backup_path, "w") as handle:
        json.dump(existing, handle, indent=2, default=str)
    print(f"\nbacked up {len(existing)} existing rows → {backup_path}")

    alloc = (
        supabase.table("user_feed_allocation")
        .select("allocation_category,allocation_slot_count,allocation_sort_order")
        .eq("follow_user_id", ASH_UID)
        .order("allocation_sort_order")
        .execute()
        .data
        or []
    )
    interests = (
        supabase.table("interests").select("interest_id,interest_slug").execute().data
        or []
    )
    slug_by_id = {str(r["interest_id"]): str(r["interest_slug"]) for r in interests}

    # Bucket existing rows by category, preserving their score order. Existing
    # SOURCE rows are dropped here — they are re-placed from the produced lists
    # (which reuse already-produced ids), so a re-run is idempotent and never
    # misclassifies a prior source reel into a topic bucket.
    existing_by_category: dict[str, list[dict[str, Any]]] = {}
    for row in existing:
        if row.get("feed_slot_kind") == "source":
            continue
        category = _category_of_existing_row(row, slug_by_id)
        existing_by_category.setdefault(category, []).append(row)

    source_by_category = {"youtube": produced_yt, "x": produced_x}

    new_rows: list[dict[str, Any]] = []
    used_story_ids: set[str] = set()
    leftover: list[dict[str, Any]] = []  # trimmed topic reels (backfill pool)

    def _topic_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "feed_user_id": ASH_UID,
            "feed_story_id": row["feed_story_id"],
            "feed_date": feed_date_iso,
            "feed_score": row.get("feed_score") or 0.0,
            "feed_matched_interest_id": row.get("feed_matched_interest_id"),
            "feed_slot_kind": row.get("feed_slot_kind") or "interest",
        }

    def _source_row(story_id: str) -> dict[str, Any]:
        return {
            "feed_user_id": ASH_UID,
            "feed_story_id": story_id,
            "feed_date": feed_date_iso,
            "feed_score": _SOURCE_FEED_SCORE,
            "feed_matched_interest_id": None,
            "feed_slot_kind": "source",
        }

    for alloc_row in alloc:
        category = alloc_row["allocation_category"]
        budget = int(alloc_row["allocation_slot_count"])
        if category in ("youtube", "x"):
            taken = source_by_category.get(category, [])[:budget]
            for story_id in taken:
                if story_id in used_story_ids:
                    continue
                new_rows.append(_source_row(story_id))
                used_story_ids.add(story_id)
        else:
            rows = existing_by_category.get(category, [])
            for index, row in enumerate(rows):
                if row["feed_story_id"] in used_story_ids:
                    continue
                if index < budget:
                    new_rows.append(_topic_row(row))
                    used_story_ids.add(row["feed_story_id"])
                else:
                    leftover.append(row)  # over budget → backfill candidate

    # Backfill toward 30 from trimmed topic reels (highest score first).
    if len(new_rows) < 30 and leftover:
        leftover.sort(key=lambda r: r.get("feed_score") or 0.0, reverse=True)
        for row in leftover:
            if len(new_rows) >= 30:
                break
            if row["feed_story_id"] in used_story_ids:
                continue
            new_rows.append(_topic_row(row))
            used_story_ids.add(row["feed_story_id"])

    new_rows = new_rows[:30]
    for position, row in enumerate(new_rows, start=1):
        row["feed_position"] = position

    # Replace ash's feed for today.
    supabase.table("daily_feeds").delete().eq("feed_user_id", ASH_UID).eq(
        "feed_date", feed_date_iso
    ).execute()
    supabase.table("daily_feeds").insert(new_rows).execute()

    kinds: dict[str, int] = {}
    for row in new_rows:
        kinds[row["feed_slot_kind"]] = kinds.get(row["feed_slot_kind"], 0) + 1
    print(f"\nrewrote ash's feed: {len(new_rows)} rows  slot_kinds={kinds}")
    source_positions = [
        (r["feed_position"], r["feed_story_id"]) for r in new_rows if r["feed_slot_kind"] == "source"
    ]
    print(f"source reels at positions: {source_positions}")
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
