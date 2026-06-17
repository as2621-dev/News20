"""Backfill interest_search_query for the picker-taxonomy leaves that ash follows.

ROOT CAUSE FIX: the recursive-picker taxonomy (ai.*, geopolitics.*, business.*,
tech.*) shipped without search queries, so interest-keyed ingestion skips them and
the daily pool collapses to the only query-bearing interest (semiconductors). This
backfills concise GDELT-DOC queries derived from each interest label so the daily
batch can ingest news that actually matches a user's selections.

Idempotent + reversible:
  * Only sets a query where one is currently null/empty (won't clobber the 4 seed
    queries already present).
  * Pass ``--revert`` to null out exactly the rows this script manages.

Usage:
    .venv/bin/python scripts/backfill_interest_queries.py          # apply
    .venv/bin/python scripts/backfill_interest_queries.py --revert # undo
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reason: GDELT queries — concise topical keyword sets, not boolean syntax. Derived
# from each interest_label so ingestion fetches the user's actual subjects. Avoid
# 1–2 char tokens (e.g. "AI", "EU", "US", "EV"): GDELT DOC rejects them ("keyword
# too short") and the BigQuery tokenizer drops <3-char tokens — spell them out.
QUERIES_BY_SLUG: dict[str, str] = {
    # ── AI (depth-1 picks under the AI tree) ──────────────────────────────────
    "ai.data-center-buildout": "artificial intelligence data center buildout capacity news",
    "ai.compute-energy-demand": "artificial intelligence compute electricity power demand news",
    "ai.alignment-research": "artificial intelligence alignment safety research news",
    "ai.interpretability": "artificial intelligence model interpretability research news",
    "ai.evals-red-teaming": "artificial intelligence model evaluation red teaming benchmark news",
    "ai.catastrophic-risk": "artificial intelligence catastrophic risk safety policy news",
    "ai.autonomous-vehicles": "autonomous vehicles self-driving cars news",
    "ai.china": "China artificial intelligence technology news",
    "ai.drones": "military drones unmanned aircraft technology news",
    "ai.eu-ai-act": "European Union artificial intelligence act regulation news",
    "ai.global-governance": "artificial intelligence global governance regulation news",
    "ai.humanoid-robots": "humanoid robots robotics news",
    "ai.us-policy": "United States artificial intelligence policy regulation news",
    # ── Arts & culture ────────────────────────────────────────────────────────
    "arts.box-office": "box office movie ticket sales news",
    "arts.fiction": "fiction novels publishing news",
    "arts.nonfiction": "nonfiction books publishing news",
    "arts.prizes-bestsellers": "book prizes bestsellers literary awards news",
    "arts.trends": "arts culture trends news",
    # ── Business & markets ────────────────────────────────────────────────────
    "business": "business markets economy finance news",
    "business.inflation": "inflation consumer prices news",
    "business.interest-rates-fed": "Federal Reserve interest rates monetary policy news",
    "business.jobs": "jobs report employment labor market news",
    "business.gdp-growth": "gross domestic product economic growth news",
    "business.recession-risk": "recession risk economic slowdown outlook news",
    "business.agricultural-commodities": "agricultural commodities wheat corn soybean prices news",
    "business.bonds": "government bonds treasury yields news",
    "business.commodities": "commodities prices futures market news",
    "business.currencies": "foreign exchange currencies forex news",
    "business.equities": "stock market equities shares news",
    "business.ipos": "initial public offering stock listing news",
    "business.leadership-executives": "corporate leadership chief executive news",
    "business.major-indices": "stock market indices Dow Jones Nasdaq news",
    "business.mergers-acquisitions": "mergers acquisitions corporate deals news",
    "business.metals-mining": "metals mining gold copper news",
    "business.stablecoins": "stablecoin cryptocurrency regulation news",
    "business.stocks-equities": "stock market shares equities news",
    # ── Climate / crypto / entertainment roots ────────────────────────────────
    "climate": "climate change environment global warming news",
    "crypto": "cryptocurrency bitcoin ethereum blockchain news",
    "entertainment": "entertainment celebrity film television news",
    # ── Environment ───────────────────────────────────────────────────────────
    "environment.batteries-storage": "battery energy storage technology news",
    "environment.carbon-markets": "carbon markets emissions trading news",
    "environment.climate-science": "climate science research warming news",
    "environment.cop-summits": "climate summit United Nations COP news",
    "environment.earthquakes": "earthquake seismic disaster news",
    "environment.electric-vehicles": "electric vehicle adoption sales news",
    "environment.emissions-targets": "carbon emissions reduction targets news",
    "environment.endangered-species": "endangered species wildlife conservation news",
    "environment.floods": "floods flooding disaster news",
    "environment.forests-habitats": "deforestation forests habitat conservation news",
    "environment.heatwaves-drought": "heatwave drought extreme weather news",
    "environment.hurricanes": "hurricane tropical storm news",
    "environment.hydrogen": "hydrogen energy fuel news",
    "environment.nuclear": "nuclear power energy reactor news",
    "environment.oceans-coral": "oceans coral reef marine news",
    "environment.solar": "solar power energy panels news",
    "environment.wildfires": "wildfire forest fire news",
    "environment.wind": "wind power energy turbines news",
    # ── Geopolitics ───────────────────────────────────────────────────────────
    "geopolitics.oil-opec": "oil prices OPEC crude production news",
    "geopolitics.natural-gas-pipelines": "natural gas pipeline energy news",
    "geopolitics.critical-minerals": "critical minerals rare earth supply chain news",
    "geopolitics.china-tariffs": "China tariffs trade war news",
    "geopolitics.export-controls-chips": "semiconductor export controls China chips news",
    "geopolitics.russia-sanctions": "Russia sanctions economy news",
    # ── Health / lifestyle roots ──────────────────────────────────────────────
    "health": "health medicine wellbeing news",
    "lifestyle": "lifestyle travel living news",
    # ── Politics ──────────────────────────────────────────────────────────────
    "politics.budget": "government budget spending fiscal news",
    "politics.campaigns": "political campaigns election news",
    "politics.education": "education policy schools news",
    "politics.executive-branch": "White House executive branch president news",
    "politics.guns": "gun control firearms policy news",
    "politics.healthcare": "healthcare policy reform news",
    "politics.immigration": "immigration policy border news",
    "politics.legislation-bills": "legislation bills Congress news",
    "politics.legislative-races": "legislative races elections Congress news",
    "politics.major-rulings": "court rulings legal decisions news",
    "politics.national-elections": "national elections presidential vote news",
    "politics.state-local": "state local government politics news",
    "politics.supreme-court": "Supreme Court rulings justices news",
    "politics.taxes": "tax policy reform news",
    # ── Science / space root ──────────────────────────────────────────────────
    "science": "space science research discovery news",
    # ── Sport ─────────────────────────────────────────────────────────────────
    "sport": "sports games results news",
    "sport.cricket": "cricket international matches news",
    "sport.soccer": "soccer football matches league news",
    # ── Tech ──────────────────────────────────────────────────────────────────
    "tech": "technology gadgets software news",
    "tech.launches-missions": "rocket launch space mission NASA SpaceX news",
    "tech.ai": "artificial intelligence machine learning news",
    "tech.consoles": "video game consoles PlayStation Xbox Nintendo news",
    "tech.data-breaches": "data breach cybersecurity hack news",
    "tech.esports": "esports competitive gaming tournaments news",
    "tech.pc-gaming": "personal computer gaming hardware news",
    "tech.ransomware": "ransomware cyberattack malware news",
    "tech.vulnerabilities": "software security vulnerabilities exploits news",
    # ── World root ────────────────────────────────────────────────────────────
    "world": "world international politics news",
}


def main() -> None:
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    sb = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    revert = "--revert" in sys.argv[1:]

    rows = (
        sb.table("interests")
        .select("interest_id,interest_slug,interest_search_query")
        .in_("interest_slug", list(QUERIES_BY_SLUG))
        .execute()
        .data
        or []
    )
    by_slug = {r["interest_slug"]: r for r in rows}
    missing = [s for s in QUERIES_BY_SLUG if s not in by_slug]
    if missing:
        print(f"WARN: slugs not found in interests table: {missing}")

    changed = 0
    for slug, query in QUERIES_BY_SLUG.items():
        row = by_slug.get(slug)
        if not row:
            continue
        current = (row.get("interest_search_query") or "").strip()
        if revert:
            # Only null rows that hold exactly the query we set (don't touch others).
            if current == query:
                sb.table("interests").update({"interest_search_query": None}).eq(
                    "interest_id", row["interest_id"]
                ).execute()
                changed += 1
                print(f"  reverted {slug}")
        else:
            if not current:
                sb.table("interests").update({"interest_search_query": query}).eq(
                    "interest_id", row["interest_id"]
                ).execute()
                changed += 1
                print(f"  set {slug:<34} -> {query!r}")
            else:
                print(f"  skip {slug:<34} (already has query {current!r})")

    print(f"\n{'REVERTED' if revert else 'APPLIED'}: {changed} rows changed.")


if __name__ == "__main__":
    main()
