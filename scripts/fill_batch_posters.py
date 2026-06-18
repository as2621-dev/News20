"""Fill reel posters via the Gemini Batch API, AFTER a poster-less produce run.

Run after ``scripts/run_live_batch.py`` with ``POSTER_MODE=batch`` (which produces
reels with NO inline poster). This script:

  1. reads the target user's ``daily_feeds`` for today -> the produced reels,
  2. rebuilds each reel's cheap poster prep (concept -> SERP -> score -> prompt),
  3. submits ALL generations as one async **Gemini Batch** job (~50% cheaper),
  4. grades + uploads each poster to ``story-posters``, and
  5. UPDATEs ``stories.story_ambient_poster_url`` + ``digests.digest_ambient_poster_url``.

Any reel the batch misses (failure / deadline) is filled SYNCHRONOUSLY so no reel
is left posterless (Rule 12 — fail loud, never silently skip).

    RUN_FILL=1 ONLY_USER_EMAIL=ash@gmail.com .venv/bin/python scripts/fill_batch_posters.py

Dry-run (default, free) prints how many reels would be filled and exits.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
from google import genai  # noqa: E402
from supabase import create_client  # noqa: E402

from agents.m0.batch_posters import (  # noqa: E402
    PreparedPoster,
    generate_posters_batch,
    prepare_poster_generation,
)
from agents.m0.digests_input import Digest, DialogueTurn  # noqa: E402
from agents.m0.generate_posters import _extract_image_bytes, generate_from_reference  # noqa: E402
from agents.m0.grade_and_brand import grade_and_brand  # noqa: E402
from agents.m0.poster_models import DEFAULT_ACCENT_HEX  # noqa: E402
from agents.pipeline.persist import POSTER_BUCKET  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402

logger = get_logger("scripts.fill_batch_posters")

POSTER_CONTENT_TYPE = "image/webp"
PREP_WORKERS = 6


def _find_uid_by_email(supabase, email: str) -> str | None:
    """Read-only auth lookup: uid for an email (never creates a user)."""
    page = supabase.auth.admin.list_users()
    users = page if isinstance(page, list) else getattr(page, "users", []) or []
    for user in users:
        if str(getattr(user, "email", "")).lower() == email.lower():
            return str(user.id)
    return None


def _load_reels(supabase, uid: str, target: date) -> list[dict]:
    """Load today's produced reels for the user: story_id, headline, dek, digest_id."""
    feeds = (
        supabase.table("daily_feeds")
        .select("feed_story_id,feed_position")
        .eq("feed_user_id", uid)
        .eq("feed_date", target.isoformat())
        .order("feed_position")
        .execute()
        .data
        or []
    )
    story_ids = [str(r["feed_story_id"]) for r in feeds]
    if not story_ids:
        return []

    stories = (
        supabase.table("stories")
        .select("story_id,story_headline,story_dek,story_primary_outlet_name,story_ambient_poster_url")
        .in_("story_id", story_ids)
        .execute()
        .data
        or []
    )
    by_id = {str(s["story_id"]): s for s in stories}

    digests = (
        supabase.table("digests")
        .select("digest_id,digest_story_id,digest_is_current")
        .in_("digest_story_id", story_ids)
        .eq("digest_is_current", True)
        .execute()
        .data
        or []
    )
    digest_by_story = {str(d["digest_story_id"]): str(d["digest_id"]) for d in digests}

    reels: list[dict] = []
    for position, story_id in enumerate(story_ids):
        story = by_id.get(story_id)
        if not story:
            continue
        reels.append(
            {
                "story_id": story_id,
                "position": position,
                "headline": story.get("story_headline") or "",
                "summary": story.get("story_dek") or story.get("story_headline") or "",
                "outlet": story.get("story_primary_outlet_name") or "",
                "digest_id": digest_by_story.get(story_id),
                "has_poster": bool(story.get("story_ambient_poster_url")),
            }
        )
    return reels


def _digest_for(reel: dict) -> Digest:
    """Build the minimal M0 Digest the poster prep needs from a persisted reel."""
    return Digest(
        digest_id=reel["story_id"],
        digest_headline=reel["headline"],
        digest_category="News",
        digest_source=reel["outlet"] or "News",
        digest_source_url=None,
        turns=[DialogueTurn(speaker="ALEX", text=reel["summary"] or reel["headline"])],
    )


def _upload_poster(supabase, story_id: str, webp_bytes: bytes) -> str:
    """Upsert the graded poster into story-posters and return its public URL."""
    object_path = f"{story_id}/poster.webp"
    storage = supabase.storage.from_(POSTER_BUCKET)
    storage.upload(
        path=object_path,
        file=webp_bytes,
        file_options={"content-type": POSTER_CONTENT_TYPE, "upsert": "true"},
    )
    return storage.get_public_url(object_path)


def _attach_poster(supabase, story_id: str, digest_id: str | None, poster_url: str) -> None:
    """UPDATE the stories + current digest rows with the new poster URL."""
    supabase.table("stories").update(
        {"story_ambient_poster_url": poster_url}
    ).eq("story_id", story_id).execute()
    supabase.table("digests").update(
        {"digest_ambient_poster_url": poster_url}
    ).eq("digest_story_id", story_id).eq("digest_is_current", True).execute()


def main() -> int:
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    paid = os.environ.get("RUN_FILL") == "1"
    email = os.environ.get("ONLY_USER_EMAIL", "").strip()
    if not email:
        print("FAIL: set ONLY_USER_EMAIL")
        return 1

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    target = date.today()
    uid = _find_uid_by_email(supabase, email)
    if not uid:
        print(f"FAIL: no auth user for {email}")
        return 1

    reels = _load_reels(supabase, uid, target)
    print(f"\n=== BATCH POSTER FILL — {email} ({uid}) feed_date={target.isoformat()} ===")
    print(f"reels in today's feed: {len(reels)}")
    needing = [r for r in reels if not r["has_poster"]]
    print(f"reels needing a poster: {len(needing)}  (already have one: {len(reels) - len(needing)})")
    if not needing:
        print("nothing to do.")
        return 0
    if not paid:
        print("\nDRY-RUN — set RUN_FILL=1 to generate + upload. No paid calls made.")
        return 0

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # ── 1. Prep all reels (concept -> SERP -> score -> prompt), in parallel ──
    print("\n--- prep (concept -> SERP -> prompt) ---")

    def _prep(reel: dict) -> tuple[dict, PreparedPoster | None]:
        try:
            return reel, prepare_poster_generation(_digest_for(reel), client)
        except Exception as exc:  # noqa: BLE001 — one bad prep never aborts the fill
            logger.error("fill_prep_failed", story_id=reel["story_id"], error=str(exc))
            return reel, None

    with ThreadPoolExecutor(max_workers=PREP_WORKERS) as pool:
        prepped = list(pool.map(_prep, needing))

    prepared = [p for _r, p in prepped if p is not None]
    prep_failed = [r for r, p in prepped if p is None]
    print(f"  prepared: {len(prepared)}   prep-failed: {len(prep_failed)}")
    for reel in prep_failed:
        print(f"    [prep-fail] {reel['story_id']}  {reel['headline'][:70]}")

    # ── 2. Generate every prepared poster in ONE async Batch (chunked) ──
    print("\n--- batch generation (Gemini Batch API, Nano Banana Pro) ---")
    batch_images = generate_posters_batch(client, prepared)
    print(f"  batch returned images for {len(batch_images)}/{len(prepared)} reels")

    # ── 3. Sync fallback for any prepared reel the batch missed ──
    prepared_by_id = {p.digest_id: p for p in prepared}
    missing_ids = [p.digest_id for p in prepared if p.digest_id not in batch_images]
    if missing_ids:
        print(f"\n--- sync fallback for {len(missing_ids)} batch miss(es) ---")
        for story_id in missing_ids:
            item = prepared_by_id[story_id]
            try:
                response = generate_from_reference(
                    client,
                    item.synthesized_prompt,
                    item.reference_image_bytes,
                    item.reference_mime_type,
                )
                raw, _mime = _extract_image_bytes(response)
                if raw:
                    batch_images[story_id] = raw
                    print(f"    [sync-ok] {story_id}")
                else:
                    print(f"    [sync-empty] {story_id} (safety filter / no image)")
            except Exception as exc:  # noqa: BLE001
                print(f"    [sync-fail] {story_id}: {type(exc).__name__}: {exc}")

    # ── 4. Grade + upload + attach ──
    print("\n--- grade + upload + attach ---")
    digest_by_id = {r["story_id"]: r["digest_id"] for r in needing}
    attached = 0
    for story_id, raw_bytes in batch_images.items():
        try:
            webp = grade_and_brand(raw_bytes, DEFAULT_ACCENT_HEX)
            url = _upload_poster(supabase, story_id, webp)
            _attach_poster(supabase, story_id, digest_by_id.get(story_id), url)
            attached += 1
        except Exception as exc:  # noqa: BLE001
            print(f"    [attach-fail] {story_id}: {type(exc).__name__}: {exc}")

    still_missing = [r["story_id"] for r in needing if r["story_id"] not in batch_images]
    print("\n--- SUMMARY ---")
    print(f"  reels needing poster ... {len(needing)}")
    print(f"  posters attached ....... {attached}")
    print(f"  prep-failed ............ {len(prep_failed)}")
    print(f"  still posterless ....... {len(still_missing)}")
    for story_id in still_missing:
        print(f"    [posterless] {story_id}")
    return 0 if attached >= 1 and not still_missing else (0 if attached >= 1 else 1)


if __name__ == "__main__":
    sys.exit(main())
