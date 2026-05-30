"""Driver: run the SERP-seeded poster pipeline for all 5 M0 digests.

    .venv/bin/python -m agents.m0.run_poster_pipeline            # all 5
    .venv/bin/python -m agents.m0.run_poster_pipeline --only 1   # just digest-1

digest-1 runs FIRST as the access/quota probe: if it hard-errors (bad key,
model unavailable), the run STOPS before burning the rest. Writes each poster to
``assets/m0/<digest>/poster.png`` and a ``selection-report.json`` beside it.
"""

from __future__ import annotations

import argparse
import sys

from google import genai

from agents.m0.build_poster_from_news import build_poster_for_digest
from agents.m0.digests_input import DIGESTS, get_digest_by_id
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("m0.run_poster_pipeline")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SERP-seeded poster pipeline for the M0 digests.")
    parser.add_argument("--only", type=int, default=None, metavar="N", help="Run only digest-N (1-5).")
    return parser


def main() -> None:
    """Entry point: build posters for all digests (or one) and summarize."""
    args = _build_arg_parser().parse_args()

    settings = Settings()
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        logger.error("gemini_api_key_missing", fix_suggestion="Set GEMINI_API_KEY in .env.")
        sys.exit(1)
    client = genai.Client(api_key=api_key)

    digests = [get_digest_by_id(f"digest-{args.only}")] if args.only else DIGESTS

    successes: list[str] = []
    failures: list[str] = []
    for index, digest in enumerate(digests):
        try:
            report = build_poster_for_digest(digest, client)
        except Exception as exc:  # noqa: BLE001 — record verbatim; stop on the digest-1 probe.
            logger.error(
                "poster_pipeline_failed", digest_id=digest.digest_id,
                error_type=type(exc).__name__, error_message=str(exc),
                fix_suggestion="Check GEMINI/SERPER keys, image-model access, and quota.",
            )
            failures.append(digest.digest_id)
            if index == 0:
                logger.error("poster_pipeline_stopped_early", digest_id=digest.digest_id,
                             fix_suggestion="First digest (probe) failed; stopping before burning the rest.")
                break
            continue

        if report.poster_path:
            successes.append(digest.digest_id)
        else:
            failures.append(f"{digest.digest_id} ({report.notes})")

    logger.info(
        "poster_pipeline_run_summary",
        success_count=len(successes), success_ids=successes, failure_ids=failures,
    )
    if not successes:
        sys.exit(1)


if __name__ == "__main__":
    main()
