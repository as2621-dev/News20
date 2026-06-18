"""Read-only preflight: confirm ash's account, allocation, and interest backing.

Prints the auth email for the known uid, his user_feed_allocation (drives the
per-category produce caps), and his followed-interest count by segment — so the
overnight 30-reel run is scoped to the RIGHT existing user (ONLY_USER_EMAIL
creates a user if the email is wrong, which we must avoid).

    .venv/bin/python scripts/preflight_ash.py
"""

from __future__ import annotations

import os
import sys

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

    # auth email for the known uid
    email = "?"
    try:
        user = supabase.auth.admin.get_user_by_id(ASH_UID)
        email = getattr(getattr(user, "user", None), "email", "?")
    except Exception as exc:  # noqa: BLE001
        email = f"lookup-failed: {type(exc).__name__}: {exc}"
    print(f"ash uid:   {ASH_UID}")
    print(f"ash email: {email}")

    alloc = (
        supabase.table("user_feed_allocation")
        .select("allocation_category,allocation_slot_count,allocation_sort_order")
        .eq("follow_user_id", ASH_UID)
        .order("allocation_sort_order")
        .execute()
        .data
        or []
    )
    total = sum(int(r["allocation_slot_count"]) for r in alloc)
    print(f"\nuser_feed_allocation ({len(alloc)} rows, total slots = {total}):")
    for r in alloc:
        print(
            f"  [{r['allocation_sort_order']:>2}] {r['allocation_category']:<16} "
            f"{r['allocation_slot_count']}"
        )

    profile = (
        supabase.table("user_interest_profile")
        .select("profile_interest_id,profile_source")
        .eq("profile_user_id", ASH_UID)
        .execute()
        .data
        or []
    )
    print(f"\nfollowed interests: {len(profile)}")

    # which followed interests carry a search query (ingestible)?
    ids = [str(r["profile_interest_id"]) for r in profile]
    ingestible = 0
    labels: list[str] = []
    for start in range(0, len(ids), 100):
        chunk = ids[start : start + 100]
        rows = (
            supabase.table("interests")
            .select("interest_id,interest_slug,interest_search_query")
            .in_("interest_id", chunk)
            .execute()
            .data
            or []
        )
        for row in rows:
            has_q = bool((row.get("interest_search_query") or "").strip())
            if has_q:
                ingestible += 1
            labels.append(f"{row['interest_slug']}{'' if has_q else ' (NO QUERY)'}")
    print(f"ingestible (has search query): {ingestible}/{len(profile)}")
    for label in sorted(labels):
        print(f"  - {label}")

    # today's feeds already present?
    from datetime import date

    today = date.today().isoformat()
    feeds = (
        supabase.table("daily_feeds")
        .select("feed_story_id")
        .eq("feed_user_id", ASH_UID)
        .eq("feed_date", today)
        .execute()
        .data
        or []
    )
    print(f"\nexisting daily_feeds for {today}: {len(feeds)} rows")


if __name__ == "__main__":
    main()
