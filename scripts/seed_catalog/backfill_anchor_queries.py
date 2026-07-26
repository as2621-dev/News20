"""Backfill soup ``interest_search_query`` values to the comma-anchor contract (issue #69).

Why this exists
---------------
The lexical relevance key (#50, ``agents/ingestion/interest_lexical.py``) defines
``interest_search_query`` as a **comma-joined list of anchor phrases**: each comma
phrase becomes ONE anchor whose tokens must appear **contiguously**. The 2026-07-04
FSR v2 catalog seed predates that contract and wrote comma-less keyword *soup*
("humanoid robots robotics news"). Such a query derives a single mega-anchor
demanding the whole sentence contiguously, so the interest matches nothing and
**silently ingests nothing** — the 2026-07-25 shortlist run returned
``rows_returned: 0`` across all 26 followed interests because of exactly this.

The founder's 26 followed interests were hand-fixed live on 2026-07-25. This script
covers the rest of the catalog: every interest whose query is soup, plus the
acronym-only interests that derive a title-gated anchor and nothing else (they can
never match on the story body/entities — ``has_hygiene_gap``).

How the new query is derived (deterministic — NO LLM, Rule 5)
-------------------------------------------------------------
``ANCHOR_QUERY_BACKFILL`` is a **curated data table**, slug → comma-joined anchor
phrases, authored by re-segmenting each interest's EXISTING soup query and label into
the phrases their author meant. This mirrors the ``ROOT_QUERIES`` precedent in
``backfill_queryless_interests.py``: a short label cannot be algorithmically expanded
into good anchors, so the data is curated once, checked in, and reviewed — never
generated at run time. Same input → same output; no model is ever called.

Every candidate is validated at PLAN time and a violation fails the whole run loud:

  * it must derive **>= 2 anchors** and **>= 1 strong** (non-title-gated) anchor,
  * it must not be soup itself (``has_uncommaed_soup_query``),
  * no anchor may be a banned standalone generic (silently dropped by the key),
  * it must actually differ from the value already in prod.

The report also annotates each new query with the tokens that appear in NEITHER the
old query nor the label ("new terms"), so a reviewer can see exactly where curation
went beyond re-segmentation instead of having to diff by eye.

Safety (prod write — founder-gated)
-----------------------------------
Dry-run is the DEFAULT and writes nothing to the database.

  * ``--apply`` additionally requires typing ``APPLY`` (or ``--yes``).
  * A JSON **snapshot** of every row's CURRENT value is written BEFORE the first
    update (``--snapshot-path``, default under ``.agents/backups/``). The dry run
    writes it too, so the rollback manifest exists before anyone is asked to approve.
    The update aborts if the snapshot cannot be written — no snapshot, no mutation.
  * The UPDATE is scoped per slug AND re-checks the exact old value
    (``where interest_slug = $1 and interest_search_query = $3``) — a row edited
    between plan and write is never clobbered, and a re-run changes zero rows
    (idempotent). Parameterized SQL only; the connection string is never logged.

Usage
-----
    # dry run: report + write the rollback snapshot, no DB writes (ALWAYS first):
    PYTHONPATH=. .venv/bin/python -m scripts.seed_catalog.backfill_anchor_queries

    # apply to prod (requires typing APPLY):
    PYTHONPATH=. .venv/bin/python -m scripts.seed_catalog.backfill_anchor_queries --apply

Env (from .env, never logged): ``SUPABASE_DB_URL`` (percent-encoded session pooler).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from agents.ingestion.interest_lexical import derive_anchor_specs
from agents.shared.logger import get_logger

logger = get_logger("seed_catalog.backfill_anchor_queries")

DEFAULT_SNAPSHOT_PATH = ".agents/backups/interest_search_query-pre69-2026-07-25.json"

# Curated slug → comma-joined anchor phrases (issue #69). Each value re-segments the
# interest's own soup query + label into the phrases their author meant; a handful add
# an obvious collocation or the expansion of an acronym (flagged as "new terms" in the
# dry-run report). Roots reuse the phrasing already curated in
# ``backfill_queryless_interests.ROOT_QUERIES`` so the two tables cannot disagree.
#
# Anchor-writing rules (all enforced by ``validate_query`` — a violation fails loud):
#   * >= 2 phrases, >= 1 of them multi-word or long enough to admit on its own;
#   * never a banned standalone generic ("federal", "india", "trust", …);
#   * remember the tokenizer eats words < 3 chars and _QUERY_STOPWORDS — "stock market"
#     tokenizes to NOTHING, "carbon markets" to "carbon". Phrases here are written to
#     survive it.
ANCHOR_QUERY_BACKFILL: dict[str, str] = {
    # ── roots ────────────────────────────────────────────────────────────────
    "business": "business, economy, corporate earnings",
    "climate": "climate change, global warming, environment",
    "health": "public health, medicine, healthcare",
    "lifestyle": "lifestyle, travel, tourism",
    "science": "space science, scientific research, astronomy, physics",
    "tech": "technology, software, gadgets",
    "world": "geopolitics, diplomacy, foreign policy",
    # ── ai ───────────────────────────────────────────────────────────────────
    "ai.autonomous-vehicles": "autonomous vehicles, self driving cars, autonomous driving",
    "ai.china": "China artificial intelligence, Chinese artificial intelligence, China technology",
    "ai.drones": "military drones, unmanned aircraft, drone strike, drones",
    "ai.eu-ai-act": "artificial intelligence act, European artificial intelligence, artificial intelligence regulation",
    "ai.global-governance": "artificial intelligence governance, global governance, artificial intelligence regulation",
    "ai.humanoid-robots": "humanoid robots, humanoid robot, robotics",
    "ai.us-policy": "artificial intelligence policy, artificial intelligence regulation, White House artificial intelligence",
    # ── arts ─────────────────────────────────────────────────────────────────
    "arts.fiction": "fiction, novels, book publishing",
    "arts.nonfiction": "nonfiction, book publishing, memoir",
    "arts.prizes-bestsellers": "book prizes, bestsellers, literary awards, bestseller list",
    "arts.trends": "arts trends, culture trends, cultural trends",
    # ── business ─────────────────────────────────────────────────────────────
    "business.agricultural-commodities": "agricultural commodities, wheat prices, corn prices, soybean",
    "business.bonds": "government bonds, treasury yields, bond yields",
    "business.commodities": "commodity prices, commodities, commodity futures",
    "business.currencies": "foreign exchange, currencies, currency trading, forex",
    "business.equities": "equities, share prices, shares",
    "business.equities.semis": "semiconductor, semiconductors, chipmaker, NVIDIA, TSMC",
    "business.ipos": "initial public offering, IPO, public listing",
    "business.leadership-executives": "corporate leadership, chief executive, executive appointment",
    "business.major-indices": "Dow Jones, Nasdaq, major indices, benchmark index",
    "business.mergers-acquisitions": "merger, acquisition, corporate deals, takeover",
    "business.metals-mining": "metals, mining, gold prices, copper prices",
    "business.stablecoins": "stablecoin, stablecoins, stablecoin regulation",
    "business.stocks-equities": "equities, share prices, shares",
    # ── environment ──────────────────────────────────────────────────────────
    "environment.batteries-storage": "battery storage, energy storage, grid storage, batteries",
    "environment.carbon-markets": "carbon credits, emissions trading, carbon pricing, carbon offset",
    "environment.climate-science": "climate science, climate research, global warming",
    "environment.cop-summits": "climate summit, United Nations climate, climate conference",
    "environment.earthquakes": "earthquake, earthquakes, seismic activity",
    "environment.electric-vehicles": "electric vehicle, electric vehicles, electric car",
    "environment.emissions-targets": "carbon emissions, emissions targets, emissions reduction, net zero",
    "environment.endangered-species": "endangered species, wildlife conservation, species extinction",
    "environment.floods": "floods, flooding, flash flood, flood damage",
    "environment.forests-habitats": "deforestation, forests, habitat conservation, rainforest",
    "environment.heatwaves-drought": "heatwave, drought, extreme heat, extreme weather",
    "environment.hurricanes": "hurricane, tropical storm, hurricane season",
    "environment.hydrogen": "hydrogen, green hydrogen, hydrogen fuel",
    "environment.nuclear": "nuclear power, nuclear reactor, nuclear energy, nuclear plant",
    "environment.oceans-coral": "coral reef, ocean warming, marine life, oceans",
    "environment.solar": "solar power, solar panels, solar energy, solar farm",
    "environment.wildfires": "wildfire, wildfires, forest fire, fire season",
    "environment.wind": "wind power, wind energy, wind turbines, offshore wind",
    # ── geopolitics ──────────────────────────────────────────────────────────
    "geopolitics.china-tariffs": "China tariffs, Chinese imports, trade war, tariff",
    "geopolitics.export-controls-chips": "export controls, chip export controls, semiconductor export",
    "geopolitics.russia-sanctions": "Russia sanctions, Russian sanctions, Russian economy",
    # ── politics ─────────────────────────────────────────────────────────────
    "politics.budget": "government budget, budget deficit, federal budget, fiscal policy",
    "politics.campaigns": "political campaign, campaign trail, election campaign",
    "politics.education": "education policy, public schools, school district, higher education",
    "politics.executive-branch": "White House, executive branch, presidential order",
    "politics.guns": "gun control, gun violence, firearms policy, gun laws",
    "politics.healthcare": "healthcare policy, healthcare reform, health care reform",
    "politics.immigration": "immigration policy, border security, deportation, immigration enforcement",
    "politics.legislation-bills": "legislation, Congress bill, House bill, Senate bill",
    "politics.legislative-races": "legislative races, Senate race, House race, midterm elections",
    "politics.major-rulings": "court ruling, appeals court, legal ruling, judicial decision",
    "politics.national-elections": "national election, presidential election, general election, election results",
    "politics.state-local": "state government, local government, state legislature, governor",
    "politics.supreme-court": "Supreme Court, Supreme Court ruling, justices",
    "politics.taxes": "tax policy, tax reform, tax cuts, income tax",
    # ── sport (incl. the acronym-only interests: keep the acronym, ADD a strong
    #    companion so the interest can also match on the body/entities) ───────
    "sport.nba": "NBA, National Basketball Association, basketball",
    "sport.nfl": "NFL, National Football League, American football",
    "sport.soccer.arsenal": "Arsenal, Arsenal football, Premier League",
    # ── tech ─────────────────────────────────────────────────────────────────
    "tech.ai": "artificial intelligence, machine learning",
    "tech.ai.llms": "large language model, large language models, OpenAI, Anthropic",
    "tech.consoles": "PlayStation, Xbox, Nintendo Switch, game console",
    "tech.data-breaches": "data breach, data breaches, cybersecurity breach, hacked",
    "tech.esports": "esports, competitive gaming, esports tournament",
    "tech.pc-gaming": "gaming hardware, graphics card, gaming laptop, video games",
    "tech.ransomware": "ransomware, ransomware attack, malware, cyberattack",
    "tech.vulnerabilities": "security vulnerability, software vulnerability, zero day, exploit",
}

_MIN_ANCHORS = 2


@dataclass(frozen=True)
class PlannedQueryChange:
    """One interest's old → new ``interest_search_query``, with review annotations.

    Attributes:
        interest_slug: The row's slug (the UPDATE's scope key).
        interest_label: The row's display label (review context).
        old_query: The verbatim value currently in prod (the UPDATE's guard value).
        new_query: The curated comma-anchor replacement.
        reason: Why the row is in the plan (``soup_query`` / ``no_strong_anchor``).
        anchor_phrases: The anchors ``new_query`` derives (what will actually match).
        new_terms: Tokens of ``new_query`` present in NEITHER the old query nor the
            label — i.e. the curated additions a reviewer should sanity-check.
    """

    interest_slug: str
    interest_label: str
    old_query: str
    new_query: str
    reason: str
    anchor_phrases: list[str]
    new_terms: list[str]


def backfill_reason(search_query: str) -> str | None:
    """Why this query needs backfilling, or None when it already meets the contract.

    Args:
        search_query: The interest's current ``interest_search_query``.

    Returns:
        ``"soup_query"`` for comma-less keyword soup (one unmatchable mega-anchor),
        ``"no_strong_anchor"`` when every derived anchor is title-gated or banned
        (the interest can never match on the story body/entities), else None.

    Example:
        >>> backfill_reason("humanoid robots robotics news")
        'soup_query'
        >>> backfill_reason("NBA")
        'no_strong_anchor'
        >>> backfill_reason("humanoid robots, robotics") is None
        True
    """
    derivation = derive_anchor_specs(search_query or "")
    if derivation.has_uncommaed_soup_query:
        return "soup_query"
    if derivation.has_hygiene_gap:
        return "no_strong_anchor"
    return None


def _tokens(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens (the ingestion tokenizer's raw first step)."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _interior_dropped_words(phrase: str) -> list[str]:
    """Words a phrase loses from BETWEEN two surviving tokens (an unmatchable anchor).

    The key drops words under 3 chars and ``_QUERY_STOPWORDS``, then joins what is left
    with ``\\s+`` — so "tariffs on China" becomes ``\\btariffs\\s+china\\b``, which demands
    the two words be ADJACENT and therefore never matches the very headline it was
    written for. A word dropped at either END is harmless ("Arsenal FC" → "arsenal").

    Example:
        >>> _interior_dropped_words("tariffs on China")
        ['on']
        >>> _interior_dropped_words("Arsenal FC")
        []
    """
    specs = derive_anchor_specs(phrase).specs
    if not specs:
        return []
    # Walk the raw words against the anchor's surviving tokens (same order) to mark
    # which were dropped, then keep only the drops with a survivor on BOTH sides.
    surviving = specs[0].anchor_phrase.split()
    kept: list[bool] = []
    cursor = 0
    for word in _tokens(phrase):
        if cursor < len(surviving) and word == surviving[cursor]:
            kept.append(True)
            cursor += 1
        else:
            kept.append(False)
    first, last = kept.index(True), len(kept) - 1 - kept[::-1].index(True)
    return [
        word
        for index, word in enumerate(_tokens(phrase))
        if first < index < last and not kept[index]
    ]


def validate_query(interest_slug: str, new_query: str) -> list[str]:
    """Assert a curated query is actually usable; return its anchor phrases.

    A curated value that the lexical key would weaken or drop is a bug in the table,
    not something to write to prod — so every violation raises (Rule 12).

    Args:
        interest_slug: The slug the value is curated for (error context).
        new_query: The candidate comma-joined anchor phrases.

    Returns:
        The anchor phrases the query derives, in order.

    Raises:
        ValueError: If the query is soup, derives fewer than two anchors, has no
            strong (non-title-gated) anchor, contains a banned standalone generic, or
            contains a phrase that loses an interior word (see
            :func:`_interior_dropped_words`).
    """
    for phrase in new_query.split(","):
        dropped = _interior_dropped_words(phrase)
        if dropped:
            raise ValueError(
                f"{interest_slug}: phrase {phrase.strip()!r} loses interior word(s) "
                f"{dropped}, so its anchor demands the surviving words be ADJACENT and "
                "can never match. fix_suggestion: rewrite the phrase without the "
                "short/stopword filler ('tariffs on China' → 'China tariffs')."
            )
    derivation = derive_anchor_specs(new_query)
    if derivation.has_uncommaed_soup_query:
        raise ValueError(
            f"{interest_slug}: curated query {new_query!r} is itself comma-less soup. "
            "fix_suggestion: separate the anchor phrases with commas."
        )
    if derivation.banned_anchors:
        raise ValueError(
            f"{interest_slug}: curated query {new_query!r} contains banned standalone "
            f"anchors {derivation.banned_anchors}. "
            "fix_suggestion: put the word inside a specific multi-word phrase."
        )
    if len(derivation.specs) < _MIN_ANCHORS:
        raise ValueError(
            f"{interest_slug}: curated query {new_query!r} derives "
            f"{len(derivation.specs)} anchor(s), need >= {_MIN_ANCHORS}. "
            "fix_suggestion: add another phrase (watch the <3-char + stopword drops — "
            "'stock market' tokenizes to nothing)."
        )
    if derivation.has_hygiene_gap:
        raise ValueError(
            f"{interest_slug}: curated query {new_query!r} has no strong anchor — every "
            "anchor is short/ambiguous and only matches in the title. "
            "fix_suggestion: add a multi-word phrase or a longer entity name."
        )
    return [spec.anchor_phrase for spec in derivation.specs]


def build_plan(interest_rows: list[dict[str, Any]]) -> list[PlannedQueryChange]:
    """Compute the old → new change set for the interests that need backfilling.

    Args:
        interest_rows: Prod rows with ``interest_slug`` / ``interest_label`` /
            ``interest_search_query``.

    Returns:
        One :class:`PlannedQueryChange` per row that needs (and gets) a new query,
        in input order. Rows already meeting the contract are absent.

    Raises:
        ValueError: If a row needs backfilling but has no curated entry (never guess
            a query), or if a curated entry fails :func:`validate_query`.
    """
    planned: list[PlannedQueryChange] = []
    unmapped: list[str] = []
    for row in interest_rows:
        slug = row["interest_slug"]
        old_query = row.get("interest_search_query") or ""
        reason = backfill_reason(old_query)
        if reason is None:
            continue
        new_query = ANCHOR_QUERY_BACKFILL.get(slug)
        if new_query is None:
            unmapped.append(f"{slug} ({reason}): {old_query!r}")
            continue
        anchor_phrases = validate_query(slug, new_query)
        if new_query == old_query:
            continue
        known = set(_tokens(old_query)) | set(_tokens(row.get("interest_label") or ""))
        new_terms = [t for t in dict.fromkeys(_tokens(new_query)) if t not in known]
        planned.append(
            PlannedQueryChange(
                interest_slug=slug,
                interest_label=row.get("interest_label") or "",
                old_query=old_query,
                new_query=new_query,
                reason=reason,
                anchor_phrases=anchor_phrases,
                new_terms=new_terms,
            )
        )
    if unmapped:
        raise ValueError(
            "interests need a backfill but have no ANCHOR_QUERY_BACKFILL entry: "
            + "; ".join(unmapped)
            + ". fix_suggestion: curate comma-separated anchor phrases for each slug "
            "(never let the script guess one)."
        )
    return planned


def format_dry_run_report(planned: list[PlannedQueryChange], scanned: int) -> str:
    """Render the founder-facing before → after listing for every planned change."""
    by_reason: dict[str, int] = {}
    for change in planned:
        by_reason[change.reason] = by_reason.get(change.reason, 0) + 1
    lines = [
        "=== interest_search_query → comma-anchor contract — DRY RUN (issue #69) ===",
        f"interests scanned: {scanned} · would update: {len(planned)} "
        f"({', '.join(f'{k}={v}' for k, v in sorted(by_reason.items())) or 'none'})",
        "",
    ]
    for change in planned:
        lines.append(f"  {change.interest_slug}  [{change.reason}]")
        lines.append(f"    before: {change.old_query}")
        lines.append(f"    after : {change.new_query}")
        lines.append(f"    anchors: {change.anchor_phrases}")
        if change.new_terms:
            lines.append(f"    new terms (not in old query/label): {change.new_terms}")
        lines.append("")
    return "\n".join(lines)


def write_snapshot(snapshot_path: str, planned: list[PlannedQueryChange]) -> None:
    """Write the rollback manifest (every row's CURRENT value); raise if it cannot be saved."""
    os.makedirs(os.path.dirname(snapshot_path) or ".", exist_ok=True)
    payload = {
        "issue": 69,
        "description": "interest_search_query values BEFORE the #69 comma-anchor backfill "
        "— restore with: update interests set interest_search_query = <old_query> "
        "where interest_slug = <interest_slug>",
        "row_count": len(planned),
        "rows": [
            {
                "interest_slug": change.interest_slug,
                "interest_label": change.interest_label,
                "old_query": change.old_query,
                "planned_new_query": change.new_query,
                "reason": change.reason,
            }
            for change in planned
        ],
    }
    with open(snapshot_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


async def fetch_interest_rows(conn: Any) -> list[dict[str, Any]]:
    """Read every interest's slug/label/query (READ-ONLY)."""
    records = await conn.fetch(
        "select interest_slug, interest_label, interest_search_query "
        "from interests order by interest_slug"
    )
    return [dict(record) for record in records]


async def apply_plan(conn: Any, planned: list[PlannedQueryChange]) -> int:
    """Write the new queries; return the number of rows actually changed.

    The UPDATE is scoped by slug AND guarded on the exact old value, so a row edited
    between plan and write is skipped rather than clobbered, and a re-run of an
    already-applied plan changes zero rows (idempotent).
    """
    statement = (
        "update interests set interest_search_query = $2 "
        "where interest_slug = $1 and interest_search_query = $3"
    )
    changed = 0
    async with conn.transaction():
        for change in planned:
            status = await conn.execute(
                statement, change.interest_slug, change.new_query, change.old_query
            )
            if status.rsplit(" ", 1)[-1] != "0":
                changed += 1
    return changed


def _confirm_apply(change_count: int, assume_yes: bool) -> bool:
    """Loud interactive gate in front of the prod write path."""
    if assume_yes:
        return True
    print(
        f"\nAbout to UPDATE interest_search_query on {change_count} production "
        "interest row(s).\nType APPLY to continue, anything else to abort: ",
        end="",
    )
    return input().strip() == "APPLY"


async def _run(args: argparse.Namespace) -> int:
    # Reason: imported lazily so the pure planning helpers (and their tests) never need
    # asyncpg or a database URL.
    from scripts.seed_catalog.seed_via_pooler import _connect

    conn = await _connect()
    try:
        rows = await fetch_interest_rows(conn)
        planned = build_plan(rows)
        print(format_dry_run_report(planned, scanned=len(rows)))

        write_snapshot(args.snapshot_path, planned)
        print(f"snapshot written (rollback manifest): {args.snapshot_path}\n")

        if not args.apply:
            logger.info(
                "anchor_query_backfill_dry_run_complete",
                interests_scanned=len(rows),
                would_update=len(planned),
                snapshot_path=args.snapshot_path,
                message="no database writes performed; re-run with --apply to write",
            )
            print("DRY RUN — nothing written to the database. Re-run with --apply.")
            return 0

        if not planned:
            print("Nothing to apply.")
            return 0
        if not _confirm_apply(len(planned), args.yes):
            print("aborted — no rows written.")
            return 1

        changed = await apply_plan(conn, planned)
        remaining = [
            row["interest_slug"]
            for row in await fetch_interest_rows(conn)
            if backfill_reason(row.get("interest_search_query") or "") is not None
        ]
        logger.info(
            "anchor_query_backfill_applied",
            rows_changed=changed,
            rows_planned=len(planned),
            remaining_nonconforming=len(remaining),
            remaining_slugs=remaining,
            snapshot_path=args.snapshot_path,
        )
        print(f"applied: {changed}/{len(planned)} rows changed.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    """CLI entry point — dry-run by default, ``--apply`` (+ typed APPLY) to write."""
    # Reason: _connect reads SUPABASE_DB_URL from the environment; load .env the same
    # way backfill_queryless_interests.py does so the module runs standalone.
    from dotenv import load_dotenv

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    load_dotenv(os.path.join(repo_root, ".env"))

    parser = argparse.ArgumentParser(
        description="Issue #69 interest_search_query comma-anchor backfill (deterministic)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to production (default: dry-run report + snapshot only).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation (only meaningful with --apply).",
    )
    parser.add_argument(
        "--snapshot-path",
        default=DEFAULT_SNAPSHOT_PATH,
        help=f"Where the pre-update snapshot is written (default: {DEFAULT_SNAPSHOT_PATH}).",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
