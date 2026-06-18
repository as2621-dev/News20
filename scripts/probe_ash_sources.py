"""Read-only probe: ash's followed sources (YT/X) + feed allocation + today's feed.

    .venv/bin/python scripts/probe_ash_sources.py
"""

from __future__ import annotations

import os
import sys
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
from supabase import create_client  # noqa: E402

ASH_UID = "b316800d-d67c-4e38-898b-ba67ca3a171d"


def main() -> None:
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    follows = (
        supabase.table("user_content_sources")
        .select("source_id,source_priority,added_via")
        .eq("user_id", ASH_UID)
        .execute()
        .data
        or []
    )
    print(f"ash follows {len(follows)} content_sources rows")
    source_ids = [f["source_id"] for f in follows]
    priority_by_id = {f["source_id"]: f["source_priority"] for f in follows}
    if source_ids:
        sources = (
            supabase.table("content_sources")
            .select(
                "source_id,content_source_type,external_id,source_name,"
                "is_curated,last_fetched_at"
            )
            .in_("source_id", source_ids)
            .execute()
            .data
            or []
        )
        by_type: dict[str, int] = {}
        for s in sources:
            by_type[s["content_source_type"]] = by_type.get(s["content_source_type"], 0) + 1
            print(
                f"  [{s['content_source_type']:<16}] "
                f"prio={priority_by_id.get(s['source_id'], '?'):<10} "
                f"{s['source_name']!r:<40} external_id={s['external_id']} "
                f"last_fetched_at={s['last_fetched_at']} curated={s['is_curated']}"
            )
        print(f"  by type: {by_type}")

    alloc = (
        supabase.table("user_feed_allocation")
        .select("allocation_category,allocation_slot_count,allocation_sort_order")
        .eq("follow_user_id", ASH_UID)
        .order("allocation_sort_order")
        .execute()
        .data
        or []
    )
    print(f"\nash allocation ({len(alloc)} rows):")
    for a in alloc:
        print(
            f"  sort={a['allocation_sort_order']:<3} "
            f"{a['allocation_category']:<12} slots={a['allocation_slot_count']}"
        )

    today = date.today().isoformat()
    feed = (
        supabase.table("daily_feeds")
        .select("feed_position,feed_slot_kind,feed_story_id")
        .eq("feed_user_id", ASH_UID)
        .eq("feed_date", today)
        .order("feed_position")
        .execute()
        .data
        or []
    )
    print(f"\nash daily_feeds for {today}: {len(feed)} rows")
    kinds: dict[str, int] = {}
    for row in feed:
        kinds[row["feed_slot_kind"]] = kinds.get(row["feed_slot_kind"], 0) + 1
    print(f"  slot_kinds: {kinds}")


if __name__ == "__main__":
    main()
