"""Ad-hoc inspection: what did a profile select, and what's in their feed?

Read-only. Usage:
    .venv/bin/python scripts/inspect_profile.py ash@gmail.com
"""

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
    sb = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    email = sys.argv[1] if len(sys.argv) > 1 else "ash@gmail.com"
    target = date.today().isoformat()
    print(f"=== INSPECT profile={email} feed_date={target} ===\n")

    # ── Resolve user_id ──────────────────────────────────────────────
    page = sb.auth.admin.list_users()
    users = page if isinstance(page, list) else getattr(page, "users", []) or []
    uid = None
    for u in users:
        if str(getattr(u, "email", "")).lower() == email.lower():
            uid = str(u.id)
            break
    if not uid:
        print(f"NO auth user with email {email}. Existing emails:")
        for u in users:
            print("   ", getattr(u, "email", "?"), str(u.id))
        return
    print(f"user_id = {uid}\n")

    # ── Interest profile ─────────────────────────────────────────────
    prof = (
        sb.table("user_interest_profile")
        .select("profile_interest_id,profile_weight,profile_source,profile_is_strict")
        .eq("profile_user_id", uid)
        .execute()
        .data
        or []
    )
    interest_rows = (
        sb.table("interests")
        .select("interest_id,interest_slug,interest_label,depth_level,interest_segment_slug")
        .execute()
        .data
        or []
    )
    by_id = {str(r["interest_id"]): r for r in interest_rows}
    print(f"--- user_interest_profile: {len(prof)} rows ---")
    for p in prof:
        r = by_id.get(str(p["profile_interest_id"]), {})
        print(
            f"   {r.get('interest_slug','?'):<28} "
            f"label={r.get('interest_label','?'):<24} "
            f"seg={r.get('interest_segment_slug')} "
            f"w={p['profile_weight']} src={p['profile_source']} strict={p['profile_is_strict']}"
        )

    # ── Entity follows ───────────────────────────────────────────────
    try:
        follows = (
            sb.table("user_entity_follows")
            .select("*")
            .eq("follow_user_id", uid)
            .execute()
            .data
            or []
        )
        print(f"\n--- user_entity_follows: {len(follows)} rows ---")
        for f in follows[:20]:
            print("   ", {k: v for k, v in f.items() if k != "follow_user_id"})
    except Exception as exc:  # noqa: BLE001
        print(f"\n(user_entity_follows query failed: {exc})")

    # ── Feed allocation ──────────────────────────────────────────────
    alloc = (
        sb.table("user_feed_allocation")
        .select("*")
        .eq("follow_user_id", uid)
        .execute()
        .data
        or []
    )
    print(f"\n--- user_feed_allocation: {len(alloc)} rows ---")
    for a in alloc:
        print("   ", {k: v for k, v in a.items() if k != "allocation_user_id"})

    # ── daily_feeds for this user (today) ────────────────────────────
    feeds = (
        sb.table("daily_feeds")
        .select("feed_story_id,feed_position,feed_score,feed_slot_kind,feed_date")
        .eq("feed_user_id", uid)
        .eq("feed_date", target)
        .execute()
        .data
        or []
    )
    print(f"\n--- daily_feeds (today {target}): {len(feeds)} rows ---")
    if not feeds:
        # check any date
        any_feeds = (
            sb.table("daily_feeds")
            .select("feed_date")
            .eq("feed_user_id", uid)
            .execute()
            .data
            or []
        )
        dates = Counter(str(r["feed_date"]) for r in any_feeds)
        print(f"   (none today) feeds on other dates: {dict(dates)}")
        feeds = (
            sb.table("daily_feeds")
            .select("feed_story_id,feed_position,feed_score,feed_slot_kind,feed_date")
            .eq("feed_user_id", uid)
            .order("feed_date", desc=True)
            .limit(40)
            .execute()
            .data
            or []
        )
        print(f"   showing most-recent {len(feeds)} feed rows instead")

    sids = [str(r["feed_story_id"]) for r in feeds]
    if sids:
        stories = (
            sb.table("stories")
            .select("story_id,story_headline,story_segment_slug,story_detail_category")
            .in_("story_id", sids)
            .execute()
            .data
            or []
        )
        smap = {str(s["story_id"]): s for s in stories}
        seg_counts: Counter = Counter()
        cat_counts: Counter = Counter()
        for r in sorted(feeds, key=lambda x: x.get("feed_position") or 0):
            s = smap.get(str(r["feed_story_id"]), {})
            seg_counts[s.get("story_segment_slug", "?")] += 1
            cat_counts[s.get("story_detail_category", "?")] += 1
            print(
                f"   #{r.get('feed_position','?'):<3} [{s.get('story_segment_slug','?'):<14}|"
                f"{str(s.get('story_detail_category','?')):<16}] {s.get('story_headline','?')[:70]}"
            )
        print(f"\n   segment distribution: {dict(seg_counts)}")
        print(f"   detail_category distribution: {dict(cat_counts)}")

    # ── Global produced pool today ───────────────────────────────────
    today_stories = (
        sb.table("stories")
        .select("story_id,story_segment_slug,story_detail_category,story_created_at")
        .gte("story_created_at", f"{target}T00:00:00")
        .execute()
        .data
        or []
    )
    print(f"\n--- GLOBAL stories created today: {len(today_stories)} ---")
    print("   segment dist:", dict(Counter(s.get("story_segment_slug", "?") for s in today_stories)))
    print("   detail_category dist:", dict(Counter(str(s.get("story_detail_category", "?")) for s in today_stories)))

    # all active profiles (context for global skew)
    all_prof = (
        sb.table("user_interest_profile")
        .select("profile_user_id")
        .execute()
        .data
        or []
    )
    print(f"\n--- total active profiles (all users): {len({str(r['profile_user_id']) for r in all_prof})} ---")


if __name__ == "__main__":
    main()
