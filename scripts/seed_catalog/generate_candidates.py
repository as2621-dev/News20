"""LLM candidate generator for the per-archetype content-source catalog (5f SP1).

The existing seeder (``scripts/seed_catalog/seed_catalog.py``) reads
``data/{type}.{archetype}.json`` candidate files and resolves each entry against
live APIs (YouTube / iTunes / Wikipedia), dropping any that fail. Until now those
candidate files were hand-curated and tiny (~4-7 entries/cell). This module fills
the only real gap for full population: it OVER-GENERATES a ranked list of real,
well-known entities per ``(entry_type, archetype)`` cell using the repo's shared
Gemini client (``agents.pipeline.llm_clients.LLMClient``) and writes them in the
exact per-axis JSON schema the seeder consumes.

Anti-hallucination contract: the generator only PROPOSES. The seeder's resolvers
independently verify each proposal and drop what does not resolve. So this module
optimises for proposing real entities with correct identifiers (handles / slugs /
titles) and validates the structural contract (identity field present, the
``topic_tags[0]`` key is one of the 8), but it does NOT itself confirm an entity
exists — that is the resolvers' job.

Pipeline per cell:
  1. Build the prompt (``prompts.build_candidate_prompt``) and call Gemini.
  2. Robustly parse the model's text into a JSON array (delegated to
     ``candidate_validation.parse_candidate_array``).
  3. Validate + normalise each candidate for the axis (delegated to
     ``candidate_validation.normalise_candidate``); drop invalid with a logged
     count (fail loud, never silent).
  4. Union with any entries already curated in the target file (so hand-picked
     sources like ``lexfridman`` are preserved), dedupe, cap to ``n``.
  5. Write ``data/{type}.{archetype}.json`` (pretty, ranked best-first).

Parsing + per-candidate validation live in ``candidate_validation.py`` (pure
functions, unit-tested in isolation); this module owns orchestration, file I/O,
and the CLI.

Usage:

    python -m scripts.seed_catalog.generate_candidates --type channels --archetype ai-frontier-tech
    python -m scripts.seed_catalog.generate_candidates --type channels --archetype ai-frontier-tech --count 110
    python -m scripts.seed_catalog.generate_candidates --all          # every type × archetype
    python -m scripts.seed_catalog.generate_candidates --all --type podcasts   # one type, all archetypes
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agents.pipeline.llm_clients import LLMClient
from agents.shared.logger import get_logger
from scripts.seed_catalog import prompts
from scripts.seed_catalog.candidate_validation import (
    candidate_dedup_key,
    normalise_candidate,
    parse_candidate_array,
)
from scripts.seed_catalog.seed_catalog import ALLOWED_ARCHETYPES, DATA_DIR

logger = get_logger("seed_catalog.generate_candidates")

# Default over-generation count per cell. Sized so that, after the resolvers drop
# unresolvable proposals, ≥50 survive. The measured YouTube forHandle resolve
# rate from SP1 informs whether 75 is enough (see the SP1 report); raise via
# --count when a cell's resolve rate is low.
DEFAULT_CANDIDATE_COUNT = 75

# The four candidate axes the seeder understands (file `{type}` segment).
GENERATABLE_TYPES: tuple[str, ...] = ("channels", "podcasts", "x", "personalities")


# ── Existing-file union (preserve curated picks) ───────────────────────────────


def _load_existing_candidates(target_path: Path) -> list[dict[str, Any]]:
    """Load any candidates already curated in the target file (best-effort).

    Args:
        target_path: The ``data/{type}.{archetype}.json`` path.

    Returns:
        The existing list of candidate dicts, or ``[]`` when the file is absent
        or unreadable (logged, never fatal).
    """
    if not target_path.exists():
        return []
    try:
        parsed = json.loads(target_path.read_text())
    except json.JSONDecodeError as exc:
        logger.warning(
            "generate_candidates_existing_file_unreadable",
            filename=target_path.name,
            error_message=str(exc),
            fix_suggestion="Existing file is not valid JSON; it will be ignored and overwritten.",
        )
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


# ── Core generation ────────────────────────────────────────────────────────────


async def generate_cell(
    entry_type: str,
    archetype: str,
    *,
    count: int = DEFAULT_CANDIDATE_COUNT,
    llm_client: LLMClient | None = None,
    data_dir: Path = DATA_DIR,
    write: bool = True,
) -> list[dict[str, Any]]:
    """Generate (and optionally write) the candidate list for one cell.

    Existing curated entries in the target file are preserved (unioned first so
    they keep their best-first rank), the model's proposals are validated and
    appended, duplicates collapse on the axis identity, and the result is capped
    to ``count``.

    Args:
        entry_type: One of ``channels`` / ``podcasts`` / ``x`` / ``personalities``.
        archetype: One of the 12 archetype slugs.
        count: Target candidate count (the model is asked for this many).
        llm_client: Injected ``LLMClient`` (constructed if None). Tests inject a mock.
        data_dir: The data directory to read/write (overridable for tests).
        write: When False, return the candidates without writing the file (dry-run).

    Returns:
        The final ranked, deduped candidate list (also written to the file unless
        ``write`` is False).

    Raises:
        ValueError: When ``entry_type`` or ``archetype`` is not recognised.
    """
    if entry_type not in GENERATABLE_TYPES:
        raise ValueError(
            f"Unknown entry_type {entry_type!r}; expected one of {GENERATABLE_TYPES}. "
            "fix_suggestion: pass --type as channels|podcasts|x|personalities."
        )
    if archetype not in ALLOWED_ARCHETYPES:
        raise ValueError(
            f"Unknown archetype {archetype!r}; expected one of the 12 slugs. "
            "fix_suggestion: pass --archetype as a valid archetype slug."
        )

    client = llm_client or LLMClient()
    system_prompt = prompts.CANDIDATE_SYSTEM_PROMPT
    user_prompt = prompts.build_candidate_prompt(entry_type, archetype, count)

    logger.info(
        "generate_cell_started",
        entry_type=entry_type,
        archetype=archetype,
        requested_count=count,
    )
    model_text = await client.call_gemini(user_prompt, system=system_prompt)
    raw_candidates = parse_candidate_array(model_text)

    target_path = data_dir / f"{entry_type}.{archetype}.json"
    existing = _load_existing_candidates(target_path)

    final = _merge_and_validate(
        existing=existing,
        proposed=raw_candidates,
        entry_type=entry_type,
        count=count,
    )

    proposed_count = len(raw_candidates)
    dropped_count = proposed_count - (len(final) - _count_valid(existing, entry_type))
    logger.info(
        "generate_cell_completed",
        entry_type=entry_type,
        archetype=archetype,
        existing_count=len(existing),
        proposed_count=proposed_count,
        final_count=len(final),
        dropped_invalid=max(dropped_count, 0),
    )

    if write:
        data_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n")
        logger.info(
            "generate_cell_written", filename=target_path.name, count=len(final)
        )
    return final


def _count_valid(candidates: list[dict[str, Any]], entry_type: str) -> int:
    """Count how many of ``candidates`` survive normalisation (for drop accounting).

    Args:
        candidates: A list of raw candidate dicts.
        entry_type: The axis.

    Returns:
        The number that normalise to a valid candidate.
    """
    return sum(1 for c in candidates if normalise_candidate(c, entry_type) is not None)


def _merge_and_validate(
    *,
    existing: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
    entry_type: str,
    count: int,
) -> list[dict[str, Any]]:
    """Validate + union existing and proposed candidates, dedupe, cap to ``count``.

    Existing curated entries are placed first (preserving their rank), then the
    model's proposals fill the remainder. Dedup is on the axis identity key so a
    proposal that repeats a curated pick collapses into it.

    Args:
        existing: Candidates already in the target file (curated picks).
        proposed: Candidates the model just proposed.
        entry_type: The axis.
        count: The cap on the final list length.

    Returns:
        The final ranked, deduped, capped candidate list.
    """
    final: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for source_list in (existing, proposed):
        for raw in source_list:
            clean = normalise_candidate(raw, entry_type)
            if clean is None:
                continue
            key = candidate_dedup_key(clean, entry_type)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            final.append(clean)
            if len(final) >= count:
                return final
    return final


async def generate_many(
    *,
    entry_types: list[str],
    archetypes: list[str],
    count: int = DEFAULT_CANDIDATE_COUNT,
    llm_client: LLMClient | None = None,
    data_dir: Path = DATA_DIR,
    write: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Generate candidate files for the cartesian product of types × archetypes.

    Cells run sequentially (one Gemini call each) so a transient failure on one
    cell does not abort the rest — a failed cell logs and yields an empty list.

    Args:
        entry_types: The axes to generate.
        archetypes: The archetype slugs to generate.
        count: Per-cell target candidate count.
        llm_client: Injected ``LLMClient`` shared across all cells (None → one is built).
        data_dir: The data directory to write into.
        write: When False, generate without writing files.

    Returns:
        ``{"{type}.{archetype}": [candidate, ...]}`` for every cell attempted.
    """
    client = llm_client or LLMClient()
    results: dict[str, list[dict[str, Any]]] = {}
    for entry_type in entry_types:
        for archetype in archetypes:
            cell_key = f"{entry_type}.{archetype}"
            try:
                results[cell_key] = await generate_cell(
                    entry_type,
                    archetype,
                    count=count,
                    llm_client=client,
                    data_dir=data_dir,
                    write=write,
                )
            except Exception as exc:  # noqa: BLE001 — one bad cell must not abort the batch
                logger.error(
                    "generate_cell_failed",
                    cell=cell_key,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:200],
                    fix_suggestion="Inspect the cell; re-run just this --type/--archetype.",
                )
                results[cell_key] = []
    return results


# ── CLI ────────────────────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> None:
    """Run the generator from parsed CLI args (single cell or --all matrix).

    Args:
        args: Parsed CLI arguments (``--type`` / ``--archetype`` / ``--all`` / ``--count``).
    """
    if args.all:
        entry_types = [args.type] if args.type else list(GENERATABLE_TYPES)
        archetypes = [args.archetype] if args.archetype else sorted(ALLOWED_ARCHETYPES)
        await generate_many(
            entry_types=entry_types, archetypes=archetypes, count=args.count
        )
        return

    await generate_cell(args.type, args.archetype, count=args.count)


def main() -> None:
    """CLI entry point for the LLM candidate generator."""
    parser = argparse.ArgumentParser(
        description="Generate ranked candidate JSON for the content-source catalog."
    )
    parser.add_argument(
        "--type",
        choices=sorted(GENERATABLE_TYPES),
        help="The candidate axis to generate (required unless --all spans all types).",
    )
    parser.add_argument(
        "--archetype",
        choices=sorted(ALLOWED_ARCHETYPES),
        help="The archetype to generate (required unless --all spans all archetypes).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate every type × archetype (optionally narrowed by --type/--archetype).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_CANDIDATE_COUNT,
        help=f"Per-cell over-generation count (default {DEFAULT_CANDIDATE_COUNT}).",
    )
    args = parser.parse_args()

    if not args.all and (not args.type or not args.archetype):
        parser.error("--type and --archetype are required unless --all is passed.")

    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
