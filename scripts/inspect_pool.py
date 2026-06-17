"""Ad-hoc: why is today's produced pool skewed, and who has feeds? Read-only."""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def main() -> None:
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    target = date.today().isoformat()

    # Today's stories with timestamps + headlines
    rows = (
        sb.table("stories")
        .select("story_id,story_headline,story_segment_slug,story_created_at,story_first_reported_utc")
        .gte("story_created_at", f"{target}T00:00:00")
        .order("story_created_at")
        .execute()
        .data
        or []
    )
    print(f"=== {len(rows)} stories created today ===")
    for r in rows:
        print(f"  {r['story_created_at'][11:19]} [{r['story_segment_slug']:<12}] {r['story_headline'][:64]}")

    print("\n=== segments table ===")
    segs = sb.table("segments").select("*").execute().data or []
    for s in segs:
        print("  ", s)

    # Root interests + their segment, for AI/geopolitics/tech/business
    print("\n=== root interests (depth 0) → segment ===")
    iro = (
        sb.table("interests")
        .select("interest_slug,interest_label,depth_level,interest_segment_slug,interest_search_query")
        .eq("depth_level", 0)
        .execute()
        .data
        or []
    )
    for r in iro:
        print(f"  {r['interest_slug']:<18} seg={r['interest_segment_slug']} q={r.get('interest_search_query')!r}")

    # All daily_feeds today: who has them
    print(f"\n=== daily_feeds for {target}: per-user counts ===")
    feeds = sb.table("daily_feeds").select("feed_user_id,feed_story_id").eq("feed_date", target).execute().data or []
    per_user = Counter(str(r["feed_user_id"]) for r in feeds)
    for uid, n in per_user.items():
        print(f"  {uid}: {n} feed rows")
    print(f"  total feed rows today: {len(feeds)}; distinct users: {len(per_user)}")

    # Which interests have a search query (ingestible) and their segment
    print("\n=== ash's followed interests → ingestible? (search query) ===")
    ash_slugs = [
        "ai.data-center-buildout", "ai.compute-energy-demand", "ai.alignment-research",
        "ai.interpretability", "ai.evals-red-teaming", "ai.catastrophic-risk",
        "geopolitics.oil-opec", "geopolitics.natural-gas-pipelines", "geopolitics.critical-minerals",
        "business.inflation", "business.interest-rates-fed", "business.jobs",
        "business.gdp-growth", "business.recession-risk", "tech.launches-missions",
    ]
    irows = (
        sb.table("interests")
        .select("interest_slug,interest_search_query,interest_segment_slug,parent_interest_id,interest_id")
        .in_("interest_slug", ash_slugs)
        .execute()
        .data
        or []
    )
    for r in irows:
        q = r.get("interest_search_query")
        print(f"  {r['interest_slug']:<32} q={'YES' if q else 'NONE':<5} seg={r['interest_segment_slug']}")


if __name__ == "__main__":
    main()
