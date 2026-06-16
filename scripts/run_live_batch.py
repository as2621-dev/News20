"""⚠ LIVE production daily batch (Phase 4a-SP3) — PAID + IRREVERSIBLE.

Wires the real clients + **live GDELT ingest** + **Phase-2c detail enrichment ON**
into :func:`agents.pipeline.daily_batch.run_daily_pipeline` — the substance the
Trigger.dev cron (4a-SP4) will later fire on a schedule. Unlike the SP4 *fixture*
e2e (``tests/agents/pipeline/sp4_e2e_fixture_run.py``, which injects a fixture pool
with enrichment OFF), this runner:

  * ingests the **live GDELT** pool for every active user's followed interests,
  * turns ``enable_detail_enrichment=True`` so each produced story also gets the
    grounded key-figure / timeline / second-analytic / 5-bullets **and** the GDELT
    coverage census (``story_analytics`` + ``detail_key_points`` + ``story_trust``),
  * scores + allocates per-user ``daily_feeds``.

SAFETY (this run costs real paid Gemini calls):
  * **Dry-run by default** — prints the preflight (active users, active interests,
    enrichment lookups) and EXITS without paying. Set ``RUN_LIVE_BATCH=1`` to pay.
  * Reels are bounded per category at the cross-user max "Build your 30" budget
    (the per-category produce cap, applied inside ``run_daily_pipeline`` after the
    gate) — so no single category can dominate the batch.
  * ``MAX_PRODUCE`` is an OPTIONAL overall ceiling on top of those caps (default 8),
    trimmed round-robin across categories so balance is preserved. Set
    ``MAX_PRODUCE=0`` to let the per-category caps be the only bound (full scale).
  * ``LOOKBACK_DAYS`` (default 2) bounds GDELT recency.
  * ``INGEST_SOURCE`` (default ``doc``) — set ``bigquery`` to ingest AND run the
    Phase-2c coverage census via the unthrottled GDELT BigQuery dataset instead of
    the rate-limited DOC API (needs ``GOOGLE_APPLICATION_CREDENTIALS``; optional
    ``GCP_BILLING_PROJECT``).

Run (dry preflight, free):
    .venv/bin/python scripts/run_live_batch.py

Run (paid, capped first run):
    RUN_LIVE_BATCH=1 MAX_PRODUCE=8 .venv/bin/python scripts/run_live_batch.py

Run (paid, full scale):
    RUN_LIVE_BATCH=1 MAX_PRODUCE=0 .venv/bin/python scripts/run_live_batch.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Reason: make the repo root importable when run as a bare script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.ingestion.adapters.gdelt_bigquery import (  # noqa: E402
    GdeltBigQueryAdapter,
)
from agents.ingestion.adapters.gdelt_doc import GdeltDocAdapter  # noqa: E402
from agents.ingestion.interest_keyed_pipeline import (  # noqa: E402
    ingest_active_interests,
)
from agents.ingestion.models import InterestNode  # noqa: E402
from agents.pipeline.categories import DEFAULT_FEED_ALLOCATION  # noqa: E402
from agents.pipeline.daily_batch import (  # noqa: E402
    DEFAULT_PER_CATEGORY_CAP,
    _load_category_allocation,
    build_story_id_resolver,
    run_daily_pipeline,
)
from agents.pipeline.produce_caps import (  # noqa: E402
    compute_category_produce_caps,
)
from agents.pipeline.llm_clients import LLMClient  # noqa: E402
from agents.pipeline.persist_helpers import load_outlets_lookup  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402
from agents.voice.gemini_tts import GeminiTTSClient  # noqa: E402

logger = get_logger("scripts.run_live_batch")

_INTEREST_COLS = (
    "interest_id,parent_interest_id,interest_slug,interest_label,depth_level,"
    "interest_segment_slug,interest_search_query"
)


def _node(row: dict[str, Any]) -> InterestNode:
    """Map an ``interests`` row to an :class:`InterestNode` (mirrors the SP4 e2e)."""
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


def build_interest_segment_lookup(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Build ``{interest_id: segment_slug}`` from the interests table.

    Depth-0 (root) rows carry ``interest_segment_slug``; leaves inherit their
    nearest ancestor's (``resolve_segment_from_tags`` contract). For every
    interest we walk up to the first ancestor that carries a segment.

    Args:
        rows: All ``interests`` rows (with parent + ``interest_segment_slug``).

    Returns:
        ``{interest_id: segment_slug}`` for every interest that resolves to one.
    """
    by_id = {str(r["interest_id"]): r for r in rows}

    def nearest_segment(start_id: str) -> str | None:
        seen: set[str] = set()
        cursor: str | None = start_id
        while cursor and cursor not in seen:
            seen.add(cursor)
            row = by_id.get(cursor)
            if not row:
                return None
            segment = row.get("interest_segment_slug")
            if segment:
                return str(segment)
            parent = row.get("parent_interest_id")
            cursor = str(parent) if parent else None
        return None

    lookup: dict[str, str] = {}
    for interest_id in by_id:
        segment = nearest_segment(interest_id)
        if segment:
            lookup[interest_id] = segment
    return lookup


def _seed_demo_users(supabase: Any, interest_rows: list[dict[str, Any]]) -> list[str]:
    """Create 2 demo users with DISTINCT ingestible interests (validation only).

    Production users onboard via the app (magic-link → Blip interest picker); this
    seeds 2 clearly-marked demo profiles so the 4a-SP3 batch has someone to
    personalize for and can prove ≥2 distinct ``daily_feeds`` on live news. Picks
    two distinct interests that carry a search query (roots first) so the two
    feeds genuinely differ.

    Args:
        supabase: Service-role client.
        interest_rows: All ``interests`` rows (to pick ingestible interests).

    Returns:
        The two created ``users.user_id``s.
    """
    ingestible = sorted(
        [r for r in interest_rows if (r.get("interest_search_query") or "").strip()],
        key=lambda r: (int(r["depth_level"]), str(r["interest_slug"])),
    )
    if len(ingestible) < 2:
        print("FAIL seed: need ≥2 ingestible interests in the taxonomy.")
        return []
    picks = [ingestible[0], ingestible[1]]
    created: list[str] = []
    for index, interest in enumerate(picks):
        email = f"demo-{interest['interest_slug'].replace('.', '-')}@news20.demo"
        uid = _get_or_create_user(supabase, email)
        # Idempotent: replace any prior profile for this demo user (re-runnable).
        supabase.table("user_interest_profile").delete().eq(
            "profile_user_id", uid
        ).execute()
        supabase.table("user_interest_profile").insert(
            {
                "profile_user_id": uid,
                "profile_interest_id": str(interest["interest_id"]),
                "profile_weight": 1.0,
                "profile_source": "typed",
                "profile_is_strict": False,
            }
        ).execute()
        created.append(uid)
        print(f"  seeded demo user {index + 1}: {email} → {interest['interest_slug']}")
    return created


def _get_or_create_user(supabase: Any, email: str) -> str:
    """Get an existing auth user's id by email, or create it (idempotent seed)."""
    try:
        return str(
            supabase.auth.admin.create_user(
                {"email": email, "email_confirm": True}
            ).user.id
        )
    except Exception:  # noqa: BLE001 — user likely already exists; find it.
        page = supabase.auth.admin.list_users()
        users = page if isinstance(page, list) else getattr(page, "users", []) or []
        for user in users:
            if str(getattr(user, "email", "")).lower() == email.lower():
                return str(user.id)
        raise


def _apply_dns_pin() -> None:
    """Optional in-process DNS pin for hosts the local resolver fails on.

    Workaround for a flaky LOCAL resolver (e.g. it times out on a Cloudflare-fronted
    Supabase host while public resolvers answer fine). Set ``DNS_PIN`` to a
    comma-separated ``host=ip`` list; ``getaddrinfo`` then returns the pinned IP for
    those hosts (TLS SNI still carries the hostname, so routing/cert validation are
    unaffected). NOT for production — the Railway / Trigger.dev runtime resolves
    normally and should never set this.
    """
    pin_spec = os.environ.get("DNS_PIN", "").strip()
    if not pin_spec:
        return
    import socket

    pins: dict[str, str] = {}
    for pair in pin_spec.split(","):
        if "=" in pair:
            host, ip = pair.split("=", 1)
            pins[host.strip()] = ip.strip()
    if not pins:
        return
    original_getaddrinfo = socket.getaddrinfo

    def _patched(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        return original_getaddrinfo(pins.get(host, host), *args, **kwargs)

    socket.getaddrinfo = _patched
    print(f"DNS_PIN active (local resolver workaround): {pins}")


async def _run() -> int:
    _apply_dns_pin()

    from dotenv import load_dotenv
    from google import genai
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    paid = os.environ.get("RUN_LIVE_BATCH") == "1"
    max_produce = int(os.environ.get("MAX_PRODUCE", "8"))
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "2"))

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    target = date.today()

    print(f"\n=== LIVE BATCH (4a-SP3) — feed_date={target.isoformat()} ===")
    print(
        f"mode={'PAID' if paid else 'DRY-RUN (free)'}  "
        f"max_produce={max_produce or 'uncapped'}  lookback_days={lookback_days}"
    )

    # ── Taxonomy + enrichment segment lookup ──────────────────────────────────
    interest_rows = (
        supabase.table("interests").select(_INTEREST_COLS).execute().data or []
    )
    interest_nodes = {str(r["interest_id"]): _node(r) for r in interest_rows}
    interest_segment_lookup = build_interest_segment_lookup(interest_rows)

    # ── Active users + their followed interest ids (the ingest unit) ──────────
    profile_rows = (
        supabase.table("user_interest_profile")
        .select("profile_user_id,profile_interest_id")
        .execute()
        .data
        or []
    )
    active_user_ids = sorted({str(r["profile_user_id"]) for r in profile_rows})

    # ── Optional: seed 2 demo users when none exist (validation only) ─────────
    if os.environ.get("SEED_DEMO_USERS") == "1" and len(active_user_ids) < 2:
        print("\n--- SEEDING DEMO USERS (no real users yet) ---")
        _seed_demo_users(supabase, interest_rows)
        profile_rows = (
            supabase.table("user_interest_profile")
            .select("profile_user_id,profile_interest_id")
            .execute()
            .data
            or []
        )
        active_user_ids = sorted({str(r["profile_user_id"]) for r in profile_rows})

    followed_ids = sorted({str(r["profile_interest_id"]) for r in profile_rows})

    # ── Phase-2c enrichment lookups (loaded once per batch) ───────────────────
    outlets_lookup = load_outlets_lookup(supabase)
    # Ingest source: the SAME adapter instance feeds BOTH the interest ingest and
    # the Phase-2c coverage census (passed as ingest_fn's adapter AND
    # run_daily_pipeline(gdelt_adapter=…)), so this one flag migrates both off the
    # rate-limited DOC API onto unthrottled BigQuery. Default DOC for safety.
    ingest_source = os.environ.get("INGEST_SOURCE", "doc").strip().lower()
    if ingest_source == "bigquery":
        gdelt_adapter: Any = GdeltBigQueryAdapter(
            billing_project=os.environ.get("GCP_BILLING_PROJECT") or None
        )
    else:
        ingest_source = "doc"
        gdelt_adapter = GdeltDocAdapter()

    print("\n--- PREFLIGHT ---")
    print(
        f"  ingest source ................. {ingest_source} "
        f"({'BigQuery (unthrottled)' if ingest_source == 'bigquery' else 'DOC API (rate-limited)'})"
    )
    print(f"  interests in taxonomy ......... {len(interest_nodes)}")
    print(f"  interest→segment lookup ....... {len(interest_segment_lookup)}")
    print(f"  active users (profiles) ....... {len(active_user_ids)}")
    print(f"  distinct followed interests ... {len(followed_ids)}")
    print(f"  outlets bias lookup ........... {len(outlets_lookup)}")

    # Per-category produce caps preview: the cross-user max "Build your 30" budget
    # per category — the ceiling on reels generated for each. This is the number
    # the run will enforce after the gate (no single category can exceed it).
    allocation_by_user = _load_category_allocation(supabase, active_user_ids)
    caps, breaking_headroom = compute_category_produce_caps(
        allocation_by_user, active_user_ids, DEFAULT_FEED_ALLOCATION
    )
    print("  per-category produce caps ....")
    if caps:
        for category, cap in sorted(caps.items()):
            print(f"      {category:<16} {cap}")
        print(f"      (breaking headroom: top-{breaking_headroom} by importance)")
    else:
        print(
            f"      none — no active users (fallback default cap = "
            f"{DEFAULT_PER_CATEGORY_CAP}/category)"
        )
    if max_produce > 0:
        print(f"  overall ceiling (MAX_PRODUCE) . {max_produce}")

    if not active_user_ids or not followed_ids:
        print(
            "\nFAIL preflight: no active users / followed interests to ingest for.\n"
            "Seed ≥2 users with user_interest_profile rows (onboarding) first."
        )
        return 1

    if not paid:
        print(
            "\nDRY-RUN complete — no paid calls made. "
            "Re-run with RUN_LIVE_BATCH=1 to produce.\n"
        )
        return 0

    # ── PAID clients ──────────────────────────────────────────────────────────
    llm_client = LLMClient()
    tts_client = GeminiTTSClient()
    poster_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resolver = build_story_id_resolver(supabase)
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    async def ingest_fn():
        result = await ingest_active_interests(
            followed_interest_ids=followed_ids,
            interest_nodes=interest_nodes,
            adapter=gdelt_adapter,
            since_utc=since,
            resolve_existing_story_ids=resolver,
        )
        stories = result.canonical_stories
        tags = result.story_interest_tags
        print(
            f"  ingest: {result.total_candidates_fetched} candidates → "
            f"{len(stories)} canonical stories"
        )
        # Reason: the volume bound is the per-category produce cap + the optional
        # MAX_PRODUCE overall ceiling, both applied INSIDE run_daily_pipeline AFTER
        # the gate (category-balanced). The old front-slice here truncated an
        # interest-ordered pool and skewed every reel into one category — removed.
        return stories, tags

    print("\n--- PAID RUN (live GDELT ingest → produce + enrich → allocate) ---")
    result = await run_daily_pipeline(
        target_date=target,
        supabase_client=supabase,
        llm_client=llm_client,
        tts_client=tts_client,
        ingest_fn=ingest_fn,
        interest_nodes=interest_nodes,
        poster_genai_client=poster_client,
        max_total_productions=max_produce,
        enable_detail_enrichment=True,
        enable_editorial_rewrite=True,
        interest_segment_lookup=interest_segment_lookup,
        outlets_lookup=outlets_lookup,
        gdelt_adapter=gdelt_adapter,
    )

    print(
        f"\ncandidate={result.candidate_story_count} "
        f"produced={result.produced_story_count} "
        f"skipped_by_gate={result.skipped_by_gate_count} "
        f"feeds_written={result.feeds.feeds_written if result.feeds else 0}"
    )

    # ── DoD READBACK ──────────────────────────────────────────────────────────
    feeds = (
        supabase.table("daily_feeds")
        .select("feed_user_id,feed_story_id")
        .eq("feed_date", target.isoformat())
        .execute()
        .data
        or []
    )
    feed_story_ids = sorted({str(r["feed_story_id"]) for r in feeds})
    feeds_by_user: dict[str, set[str]] = {}
    for row in feeds:
        feeds_by_user.setdefault(str(row["feed_user_id"]), set()).add(
            str(row["feed_story_id"])
        )
    analytics = (
        supabase.table("story_analytics").select("*").execute().data or []
        if feed_story_ids
        else []
    )
    keypoints = (
        supabase.table("detail_key_points").select("*").execute().data or []
        if feed_story_ids
        else []
    )

    distinct_feeds = len({frozenset(s) for s in feeds_by_user.values()})
    print("\n--- DoD CHECKS ---")
    checks = [
        (
            "≥1 story produced this run",
            result.produced_story_count >= 1,
            f"produced={result.produced_story_count}",
        ),
        (
            "daily_feeds written for ≥2 users",
            len(feeds_by_user) >= 2,
            f"users={len(feeds_by_user)}",
        ),
        ("≥2 DISTINCT user feeds", distinct_feeds >= 2, f"distinct={distinct_feeds}"),
        ("story_analytics non-empty", len(analytics) >= 1, f"rows={len(analytics)}"),
        ("detail_key_points non-empty", len(keypoints) >= 1, f"rows={len(keypoints)}"),
    ]
    all_ok = True
    for label, ok, detail in checks:
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}  ({detail})")

    print(f"\nfeed users: {list(feeds_by_user)}")
    return 0 if all_ok else 1


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
