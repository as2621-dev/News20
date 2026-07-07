"""Tally GDELT ``V2Themes`` codes on stories with NO whitelisted theme (issue #35).

Evidence tool for DATA-DRIVEN ``THEME_CATEGORY_WHITELIST`` expansion: runs the real
production ingestion path (``GdeltBigQueryAdapter`` over the live active-interest
set from prod Supabase; bodies OFF, zero DB writes) and, for every canonical story
whose aggregated ``canonical_themes`` contain ZERO whitelist codes (the stories
whose category falls back to the fetching interest's root), counts each theme code
once per story. Output = the observed top-miss codes, so whitelist entries are
backed by real counts, never guessed.

Run (read-only; needs SUPABASE_* + GOOGLE_APPLICATION_CREDENTIALS in .env):
    .venv/bin/python scripts/theme_miss_counts.py

2026-07-07 baseline (feeding the issue #35 expansion): 2,368 canonical stories,
1,213 whitelist-hit, 271 zero-theme, 884 with no whitelisted theme.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_REPO_ROOT, ".env"))

from agents.ingestion.adapters.gdelt_bigquery import GdeltBigQueryAdapter  # noqa: E402
from agents.ingestion.interest_keyed_pipeline import (  # noqa: E402
    ingest_active_interests,
)
from agents.ingestion.models import InterestNode  # noqa: E402
from agents.pipeline.theme_category import THEME_CATEGORY_WHITELIST  # noqa: E402

_INTEREST_COLS = (
    "interest_id,parent_interest_id,interest_slug,interest_label,depth_level,"
    "interest_search_query"
)


def _node(row: dict[str, Any]) -> InterestNode:
    """Map an ``interests`` row to an :class:`InterestNode` (mirrors run_live_batch)."""
    return InterestNode(
        interest_id=str(row["interest_id"]),
        parent_interest_id=(
            str(row["parent_interest_id"]) if row.get("parent_interest_id") else None
        ),
        interest_slug=str(row["interest_slug"]),
        interest_label=str(row.get("interest_label") or row["interest_slug"]),
        depth_level=int(row["depth_level"]),
        interest_search_query=row.get("interest_search_query"),
    )


async def main() -> int:
    """Ingest one production-shaped batch and print the top-miss theme codes."""
    from supabase import create_client

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    interest_rows = (
        supabase.table("interests").select(_INTEREST_COLS).execute().data or []
    )
    interest_nodes = {str(r["interest_id"]): _node(r) for r in interest_rows}
    profile_rows = (
        supabase.table("user_interest_profile")
        .select("profile_interest_id")
        .execute()
        .data
        or []
    )
    followed_ids = [str(r["profile_interest_id"]) for r in profile_rows]
    print(f"interests={len(interest_nodes)} followed_rows={len(followed_ids)}")

    adapter = GdeltBigQueryAdapter(
        billing_project=os.environ.get("GCP_BILLING_PROJECT") or None
    )
    since = datetime.now(timezone.utc) - timedelta(days=1)
    result = await ingest_active_interests(
        followed_ids,
        interest_nodes,
        adapter,
        since_utc=since,
        extract_bodies=False,
    )

    miss_counter: Counter[str] = Counter()
    miss_stories = 0
    themed_stories = 0
    no_theme_stories = 0
    for story in result.canonical_stories:
        themes = story.canonical_themes
        if not themes:
            no_theme_stories += 1
            continue
        if any(theme in THEME_CATEGORY_WHITELIST for theme in themes):
            themed_stories += 1
            continue
        miss_stories += 1
        # Reason: count each code once per STORY (set), not per repeat — the
        # whitelist decision is "how many stories would this code have rescued".
        miss_counter.update(set(themes))

    print(f"\ncanonical_stories={len(result.canonical_stories)}")
    print(f"whitelist_hit_stories={themed_stories}")
    print(f"zero_theme_stories={no_theme_stories}")
    print(f"no_whitelisted_theme_stories={miss_stories}")
    print("\nTop 60 theme codes on no-whitelisted-theme stories (story-level counts):")
    for code, count in miss_counter.most_common(60):
        print(f"{count:5d}  {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
