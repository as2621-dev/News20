"""Run the per-niche coverage census — the ≥60% hit-rate instrument (slice #5).

Reads the stored ``story_interests`` tags for the 3 seeded personas over a UTC-date
window and prints a per-niche coverage report: direct-tagged candidate counts per
interest per day, ladder-context (fallback) counts, and the hit rate against the
≥ 60% target (per persona + overall). READ-ONLY — it never triggers ingestion or
mutates the pool (see ``agents/pipeline/coverage_census.fetch_census_inputs``).

Run it AFTER the daily batch to answer: "would each persona's sections fill from
direct niche stories today?" Over ≥ 3 real pull days it is the M2/M4 go/no-go read.

Usage:
    .venv/bin/python scripts/coverage_census.py                 # last 7 UTC days
    .venv/bin/python scripts/coverage_census.py --days 3        # last 3 UTC days
    .venv/bin/python scripts/coverage_census.py --start-date 2026-07-01 \\
        --end-date 2026-07-03
    .venv/bin/python scripts/coverage_census.py --json          # machine JSON (ops)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.pipeline.coverage_census import (  # noqa: E402
    build_coverage_census,
    fetch_census_inputs,
    render_report_text,
    window_dates,
)
from agents.shared.logger import get_logger  # noqa: E402

logger = get_logger("scripts.coverage_census")


def _resolve_persona_emails() -> list[str]:
    """The 3 seeded persona emails, from ``seed_personas.PERSONA_SPECS`` (SSOT)."""
    from scripts.seed_personas import PERSONA_SPECS

    return [persona.persona_email for persona in PERSONA_SPECS]


def _resolve_window(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve the inclusive UTC date window from CLI args.

    ``--start-date``/``--end-date`` win when given; otherwise the window is the last
    ``--days`` UTC calendar dates ending today (UTC).
    """
    today_utc = datetime.now(timezone.utc).date()
    end_date = args.end_date or today_utc.isoformat()
    if args.start_date:
        start_date = args.start_date
    else:
        end = datetime.fromisoformat(end_date).date()
        start_date = (end - timedelta(days=max(args.days - 1, 0))).isoformat()
    return start_date, end_date


def main() -> int:
    """Fetch stored tags, build the census, and print the report (or JSON)."""
    parser = argparse.ArgumentParser(description="Per-niche coverage census (slice #5).")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window size in UTC days ending today (ignored if --start-date given).",
    )
    parser.add_argument("--start-date", help="Inclusive UTC lower bound (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="Inclusive UTC upper bound (YYYY-MM-DD).")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the CoverageCensusReport as JSON (for ops notes / slice #10).",
    )
    args = parser.parse_args()

    start_date, end_date = _resolve_window(args)
    persona_emails = _resolve_persona_emails()

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    personas, tag_observations, present_days = fetch_census_inputs(
        supabase,
        persona_emails=persona_emails,
        window_start_date=start_date,
        window_end_date=end_date,
    )
    report = build_coverage_census(
        personas=personas,
        tag_observations=tag_observations,
        present_days=present_days,
        window_days=window_dates(start_date, end_date),
    )

    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_report_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
