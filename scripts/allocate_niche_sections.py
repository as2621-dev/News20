"""Run the niche-section allocator for one user and persist their sections (slice #6).

Loads a user's deep ``user_interest_profile`` rows ⋈ their ``interests`` nodes, runs
:func:`agents.pipeline.niche_allocation.build_niche_allocation`, and (unless
``--dry-run``) writes the plan to ``user_feed_allocation`` via
:func:`write_user_niche_allocation`. This is the slice #6 demoable: named niche sections
+ a beyond-bubble reserve + untouched youtube/x source slots for a deep-profile persona;
a roots-only user's allocation stays the coarse baseline.

Usage:
    .venv/bin/python scripts/allocate_niche_sections.py persona.cricket@news20.seed
    .venv/bin/python scripts/allocate_niche_sections.py persona.cricket@news20.seed --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.pipeline.niche_allocation import (  # noqa: E402
    ProfileInterestForAllocation,
    build_niche_allocation,
    write_user_niche_allocation,
)
from agents.ingestion.models import InterestNode  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402

logger = get_logger("scripts.allocate_niche_sections")


def _resolve_user_id(supabase: Any, email: str) -> str | None:
    """Find an auth user's id by email, walking paginated pages (prod has many users)."""
    page_number = 1
    while True:
        page = supabase.auth.admin.list_users(page=page_number, per_page=200)
        users = page if isinstance(page, list) else getattr(page, "users", []) or []
        if not users:
            return None
        for user in users:
            if str(getattr(user, "email", "")).lower() == email.lower():
                return str(user.id)
        page_number += 1


def _load_profile_for_allocation(
    supabase: Any, user_id: str
) -> tuple[list[ProfileInterestForAllocation], dict[str, InterestNode]]:
    """Load a user's profile rows ⋈ their interest nodes into allocator inputs."""
    profile_rows = (
        supabase.table("user_interest_profile")
        .select("profile_interest_id,profile_weight,profile_display_label")
        .eq("profile_user_id", user_id)
        .execute()
        .data
        or []
    )
    interest_ids = [str(row["profile_interest_id"]) for row in profile_rows]
    node_rows = (
        supabase.table("interests")
        .select("interest_id,parent_interest_id,interest_slug,interest_label,depth_level")
        .in_("interest_id", interest_ids)
        .execute()
        .data
        if interest_ids
        else []
    ) or []
    interest_nodes: dict[str, InterestNode] = {
        str(row["interest_id"]): InterestNode(
            interest_id=str(row["interest_id"]),
            parent_interest_id=(
                str(row["parent_interest_id"]) if row.get("parent_interest_id") else None
            ),
            interest_slug=str(row["interest_slug"]),
            interest_label=str(row["interest_label"]),
            depth_level=int(row["depth_level"]),
        )
        for row in node_rows
    }
    profile: list[ProfileInterestForAllocation] = []
    for row in profile_rows:
        interest_id = str(row["profile_interest_id"])
        node = interest_nodes.get(interest_id)
        profile.append(
            ProfileInterestForAllocation(
                allocation_profile_interest_id=interest_id,
                allocation_interest_slug=node.interest_slug if node else "",
                allocation_display_label=row.get("profile_display_label"),
                allocation_profile_weight=float(row["profile_weight"]),
            )
        )
    return profile, interest_nodes


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the niche-section allocator.")
    parser.add_argument("email", help="The user's email (e.g. persona.cricket@news20.seed).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Build + print the plan without writing."
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    user_id = _resolve_user_id(supabase, args.email)
    if not user_id:
        print(f"NO auth user with email {args.email}")
        return 1
    print(f"\n=== NICHE ALLOCATION  email={args.email}  user_id={user_id} ===")

    profile, interest_nodes = _load_profile_for_allocation(supabase, user_id)
    print(f"  loaded {len(profile)} profile interest(s)")
    rows = build_niche_allocation(profile, interest_nodes)

    print(f"\n  --- PLAN ({len(rows)} rows, {sum(r.allocation_slot_count for r in rows)} slots) ---")
    for row in sorted(rows, key=lambda r: r.allocation_sort_order):
        kind = (
            "niche"
            if row.allocation_interest_id
            else ("beyond" if row.allocation_section_label else "coarse/source")
        )
        label = row.allocation_section_label or "-"
        print(
            f"    [{row.allocation_sort_order:>2}] {kind:<13} {row.allocation_category:<12}"
            f" x{row.allocation_slot_count}  label={label!r}"
            f" node={row.allocation_interest_id or '-'}"
        )

    if args.dry_run:
        print("\n  DRY-RUN — no writes.\n")
        return 0

    written = write_user_niche_allocation(supabase, user_id, rows)
    print(f"\n  WROTE {written} rows to user_feed_allocation.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
