"""Run the selection-level coverage census over a shortlist artifact (slice #10).

The ≥60% hit-rate read for a run HALTED at the shortlist rung — the only census
that works under the founder's selection-first / produce-once spend contract, since
a halted run produces nothing and so writes no ``story_interests`` tags for
``scripts/coverage_census.py`` to read. Metric definition is unchanged; only the
observation point moves (see :mod:`agents.pipeline.selection_census`).

READ-ONLY: reads the on-disk artifact plus each persona's profile rows. It never
ingests, never produces, and never writes.

Usage:
    .venv/bin/python scripts/selection_census.py                      # today's artifact
    .venv/bin/python scripts/selection_census.py --feed-date 2026-07-26
    .venv/bin/python scripts/selection_census.py --json               # machine JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.pipeline.selection_census import (  # noqa: E402
    CensusFollowedInterest,
    CensusPersonaInput,
    build_selection_census,
    render_selection_census_text,
)
from agents.shared.logger import get_logger  # noqa: E402

logger = get_logger("scripts.selection_census")

SHORTLIST_DIR = os.path.join(_REPO_ROOT, ".agents", "shortlists")


def _load_artifact(feed_date: str) -> tuple[dict[str, Any], str]:
    """Load ``.agents/shortlists/<date>-shortlist.json``, tolerating the pre-#67 shape."""
    path = os.path.join(SHORTLIST_DIR, f"{feed_date}-shortlist.json")
    with open(path) as handle:
        raw = json.load(handle)
    # Reason: artifacts written before 2026-07-25 are a bare list with no envelope.
    if isinstance(raw, list):
        return {
            "shortlist_entries": raw,
            "shortlist_run": {"run_feed_date": feed_date},
        }, path
    return raw, path


def _fetch_persona_inputs(
    supabase: Any, selection_by_user_id: dict[str, list[str]]
) -> list[CensusPersonaInput]:
    """Resolve each persona's followed interests + their selected cut."""
    from scripts.seed_personas import PERSONA_SPECS

    user_id_by_email: dict[str, str] = {}
    page_number = 1
    while True:
        page = supabase.auth.admin.list_users(page=page_number, per_page=200)
        users = page if isinstance(page, list) else getattr(page, "users", []) or []
        if not users:
            break
        for user in users:
            if user.email:
                user_id_by_email[str(user.email).lower()] = str(user.id)
        page_number += 1

    persona_inputs: list[CensusPersonaInput] = []
    for persona in PERSONA_SPECS:
        user_id = user_id_by_email.get(persona.persona_email.lower())
        followed: list[CensusFollowedInterest] = []
        if user_id:
            profile_rows = (
                supabase.table("user_interest_profile")
                .select("profile_interest_id,profile_display_label")
                .eq("profile_user_id", user_id)
                .execute()
                .data
            )
            interest_ids = [row["profile_interest_id"] for row in profile_rows]
            nodes = (
                supabase.table("interests")
                .select("interest_id,interest_slug,interest_label")
                .in_("interest_id", interest_ids)
                .execute()
                .data
                if interest_ids
                else []
            )
            node_by_id = {node["interest_id"]: node for node in nodes}
            for row in profile_rows:
                node = node_by_id.get(row["profile_interest_id"])
                if not node:
                    # Reason: a profile row pointing at a deleted node is a data
                    # fault, not a miss — name it rather than scoring it silently.
                    logger.warning(
                        "selection_census_profile_node_missing",
                        persona_key=persona.persona_key,
                        interest_id=row["profile_interest_id"],
                        fix_suggestion="Profile row references an absent interests row.",
                    )
                    continue
                followed.append(
                    CensusFollowedInterest(
                        interest_slug=node["interest_slug"],
                        interest_label=row.get("profile_display_label")
                        or node.get("interest_label")
                        or "",
                    )
                )
        persona_inputs.append(
            CensusPersonaInput(
                persona_key=persona.persona_key,
                persona_email=persona.persona_email,
                persona_user_id=user_id,
                followed_interests=followed,
                selected_story_ids=selection_by_user_id.get(user_id or "", []),
            )
        )
    return persona_inputs


def main() -> int:
    """Load the artifact, score the personas, print the report (or JSON)."""
    parser = argparse.ArgumentParser(description="Selection-level coverage census.")
    parser.add_argument(
        "--feed-date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="Shortlist artifact feed date (YYYY-MM-DD). Defaults to today UTC.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine JSON.")
    parser.add_argument(
        "--out",
        help="Write the machine JSON to this path. Use this rather than shell "
        "redirection: the structured logger and the Supabase HTTP client both "
        "write to stdout, so a redirected --json capture is NOT parseable JSON.",
    )
    args = parser.parse_args()

    artifact, path = _load_artifact(args.feed_date)
    entries = artifact.get("shortlist_entries") or []
    selection = artifact.get("shortlist_selection") or {}
    selection_by_user_id = {
        str(cut.get("selection_user_id")): list(cut.get("selection_story_ids") or [])
        for cut in (selection.get("selection_by_user") or [])
    }

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    has_selection_block = artifact.get("shortlist_selection") is not None
    production_story_ids = (
        set(selection.get("selection_production_story_ids") or [])
        if has_selection_block
        else None
    )

    report = build_selection_census(
        personas=_fetch_persona_inputs(supabase, selection_by_user_id),
        shortlist_entries=entries,
        feed_date=(artifact.get("shortlist_run") or {}).get(
            "run_feed_date", args.feed_date
        ),
        has_selection_block=has_selection_block,
        production_story_ids=production_story_ids,
    )

    if args.out:
        with open(args.out, "w") as handle:
            handle.write(report.model_dump_json(indent=2))
        print(f"wrote {args.out}")
    elif args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(f"artifact: {path}\n")
        print(render_selection_census_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
