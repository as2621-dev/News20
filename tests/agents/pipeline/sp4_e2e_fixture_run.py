"""⚠ LIVE end-to-end BATCH run for Phase 1d SP4 (PAID + IRREVERSIBLE).

The ONE real daily-pipeline run the SP4 DoD requires. It makes REAL paid Gemini
TTS + image + LLM calls (one per produced story) and REAL Supabase service-role
writes (auth users, profiles, stories, digests, daily_feeds). It is **not** a
pytest test (no ``test_`` prefix) and is guarded behind ``RUN_LIVE_E2E=1`` so a
plain ``pytest`` never triggers paid calls.

ISOLATION (mirrors the SP3 fixture run): the candidate pool is a deterministic,
ancestor-tagged FIXTURE pool (not live GDELT), so the strict-user-no-fallback and
niche-reaches-broad invariants are provable, not luck. Live-GDELT ingest at scale
stays the production ``ingest_fn`` (``agents.ingestion.interest_keyed_pipeline``);
this run injects the fixture pool through the SAME ``run_daily_pipeline`` seam.

WHAT IT PROVES (the live SP4 DoD — the weight-bounds + allocator invariants are
covered by the hermetic unit suites test_session_processor / test_feed_assembly):
  a. ≥10 produced digests (floor).
  b. ≥2 DISTINCT per-user daily_feeds, each ordered 01..N.
  c. the STRICT cricket-only user gets ONLY cricket.india leaf stories — no
     upward-fallback rows, no exploration rows.
  d. an ancestor-tagged niche soccer story reaches the BROAD sport follower
     (via the grandparent tag), and the broad user gets an exploration row the
     strict user does not.

Run:
    RUN_LIVE_E2E=1 .venv/bin/python tests/agents/pipeline/sp4_e2e_fixture_run.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone

# Reason: make the repo root importable when run as a bare script.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.ingestion.models import (  # noqa: E402
    CanonicalStory,
    InterestNode,
    StoryInterestTag,
)
from agents.pipeline.daily_batch import (  # noqa: E402
    load_active_user_inputs,
    run_daily_pipeline,
)
from agents.pipeline.feed_assembly import ScoredCandidate  # noqa: E402
from agents.pipeline.llm_clients import LLMClient  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402
from agents.voice.gemini_tts import GeminiTTSClient  # noqa: E402

logger = get_logger("sp4.e2e_fixture_run")

# Slugs resolved to live interest ids at runtime (robust across environments).
_SLUGS = [
    "sport",
    "sport.cricket",
    "sport.cricket.india",
    "sport.soccer",
    "sport.soccer.arsenal",
    "world",
]

# ── Fixture story bodies — concise, single-source, grounding-friendly so the
# verifier publishes (an ungrounded script HALTS, per Decision #5). ────────────
_CRICKET_BODIES = [
    (
        "India beat Australia by six wickets in the third one-day international in "
        "Mumbai on Saturday. Chasing 274, India reached the target with 11 balls to "
        "spare, led by an unbeaten 102 from Shubman Gill. Captain Rohit Sharma said "
        "the win gave India a 2-1 lead in the five-match series. The next match is "
        "in Chennai on Tuesday."
    ),
    (
        "The BCCI announced on Friday that India will host a day-night Test against "
        "South Africa in Kolkata in December. The board said it will be India's "
        "second pink-ball Test at Eden Gardens. Secretary Jay Shah said ticket sales "
        "open next week. The match is scheduled to run five days from December 12."
    ),
    (
        "Jasprit Bumrah took five wickets for 28 runs as India bowled out New Zealand "
        "for 162 in the second Test in Bengaluru on Thursday. It was Bumrah's tenth "
        "five-wicket haul in Tests. India lead by 134 runs at the close of day two. "
        "Coach Rahul Dravid praised the pace attack after play."
    ),
    (
        "India's women's cricket team qualified for the World Cup final after beating "
        "England by four runs in a tense semi-final in Navi Mumbai on Wednesday. "
        "Smriti Mandhana scored 78 from 65 balls. Captain Harmanpreet Kaur said the "
        "team had waited years for this moment. The final is on Sunday."
    ),
]
_ARSENAL_BODIES = [
    (
        "Arsenal beat Manchester City 2-1 at the Emirates on Sunday to go top of the "
        "Premier League. Bukayo Saka scored the winner in the 84th minute. Manager "
        "Mikel Arteta said it was the team's most complete performance of the season. "
        "Arsenal are now two points clear with ten games left."
    ),
    (
        "Arsenal signed midfielder Martin Zubimendi from Real Sociedad for a reported "
        "60 million pounds on Monday. The club said the Spain international signed a "
        "five-year contract. Sporting director Edu said Zubimendi strengthens the "
        "midfield. He is expected to make his debut against Brighton on Saturday."
    ),
    (
        "Arsenal's Gabriel Jesus will be out for three months after surgery on a knee "
        "injury, the club confirmed on Tuesday. The striker was hurt in the FA Cup "
        "win over Tottenham. Arteta said the squad would adapt. Jesus had scored "
        "eight goals this season before the injury."
    ),
    (
        "Arsenal drew 1-1 with Liverpool at Anfield on Saturday in a match that kept "
        "them in the title race. Declan Rice equalised in the second half after Mohamed "
        "Salah had given Liverpool the lead. Arteta said a point at Anfield was a fair "
        "result. The two sides are separated by a single point."
    ),
]
_SPORT_BODIES = [
    (
        "The International Olympic Committee confirmed on Monday that the 2036 Summer "
        "Games shortlist has been cut to three cities. IOC president said the final "
        "host will be chosen next year. The three cities each presented plans focused "
        "on existing venues. A decision is expected at the session in Athens."
    ),
    (
        "Novak Djokovic reached the final of the season-ending championships in Turin "
        "on Saturday after beating Carlos Alcaraz in three sets. It was Djokovic's "
        "ninth appearance in the final. He said his form had peaked at the right time. "
        "He will face Jannik Sinner on Sunday."
    ),
    (
        "World Athletics approved new rules on Tuesday allowing a fourth attempt in "
        "field events at major championships from next season. The governing body said "
        "the change is meant to improve the spectator experience. Several athletes "
        "welcomed the move. It takes effect at the next world championships."
    ),
]
_WORLD_BODIES = [
    (
        "The United Nations Security Council voted on Friday to extend its peacekeeping "
        "mission in the region for another year. Fourteen members voted in favour with "
        "one abstention. The secretary-general welcomed the renewal. The mission has "
        "operated in the area since 2011."
    ),
]


def _node(row: dict) -> InterestNode:
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


def _story(
    story_id: str, headline: str, body: str, outlet_count: int
) -> CanonicalStory:
    now = datetime.now(timezone.utc)
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=headline,
        canonical_url=f"https://reuters.com/{story_id}",
        canonical_normalized_url=f"https://reuters.com/{story_id}",
        canonical_published_utc=now,
        canonical_primary_outlet_domain="reuters.com",
        canonical_primary_outlet_name="Reuters",
        canonical_body_text=body,
        canonical_representative_external_id=f"https://reuters.com/{story_id}",
        covering_outlets=["reuters.com", "apnews.com", "bbc.com", "cnn.com"][
            : min(outlet_count, 4)
        ],
        story_outlet_count=outlet_count,
    )


def _tags(story_id: str, pairs: list[tuple[str, int]]) -> list[StoryInterestTag]:
    return [
        StoryInterestTag(
            story_interest_story_id=story_id,
            story_interest_interest_id=interest_id,
            story_interest_match_depth=depth,
        )
        for interest_id, depth in pairs
    ]


async def _run() -> int:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    from google import genai
    from supabase import create_client

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    poster_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    llm_client = LLMClient()
    tts_client = GeminiTTSClient()

    runtag = uuid.uuid4().hex[:8]
    target = date.today()
    print(f"\n=== SP4 LIVE E2E — runtag={runtag} feed_date={target.isoformat()} ===")

    # ── Resolve the real taxonomy ids by slug ─────────────────────────────────
    rows = (
        supabase.table("interests")
        .select(
            "interest_id,interest_slug,interest_label,depth_level,"
            "parent_interest_id,interest_search_query"
        )
        .in_("interest_slug", _SLUGS)
        .execute()
        .data
        or []
    )
    by_slug = {r["interest_slug"]: r for r in rows}
    missing = [s for s in _SLUGS if s not in by_slug]
    if missing:
        print(f"FAIL: interests not seeded in live DB: {missing}")
        return 1
    interest_nodes = {str(r["interest_id"]): _node(r) for r in rows}
    cricket_india_id = str(by_slug["sport.cricket.india"]["interest_id"])
    cricket_id = str(by_slug["sport.cricket"]["interest_id"])
    sport_id = str(by_slug["sport"]["interest_id"])
    soccer_id = str(by_slug["sport.soccer"]["interest_id"])
    arsenal_id = str(by_slug["sport.soccer.arsenal"]["interest_id"])
    world_id = str(by_slug["world"]["interest_id"])

    # ── Build the fixture pool (deterministic ids; ancestor-tagged) ───────────
    pool: list[CanonicalStory] = []
    tags: list[StoryInterestTag] = []
    group_of: dict[str, str] = {}

    def add(group: str, n: int, headline: str, body: str, pairs, outlets: int) -> str:
        sid = f"FIXTURE-SP4-{runtag}-{group}-{n}"
        pool.append(_story(sid, headline, body, outlets))
        tags.extend(_tags(sid, pairs))
        group_of[sid] = group
        return sid

    cricket_ids: set[str] = set()
    for i, body in enumerate(_CRICKET_BODIES, start=1):
        cricket_ids.add(
            add(
                "cricket-india",
                i,
                f"FIXTURE-SP4 India cricket update {i}",
                body,
                [(cricket_india_id, 0), (cricket_id, 1), (sport_id, 2)],
                7,
            )
        )
    arsenal_ids: set[str] = set()
    for i, body in enumerate(_ARSENAL_BODIES, start=1):
        arsenal_ids.add(
            add(
                "arsenal",
                i,
                f"FIXTURE-SP4 Arsenal update {i}",
                body,
                [(arsenal_id, 0), (soccer_id, 1), (sport_id, 2)],
                8,
            )
        )
    for i, body in enumerate(_SPORT_BODIES, start=1):
        add(
            "sport-gen",
            i,
            f"FIXTURE-SP4 Sport update {i}",
            body,
            [(sport_id, 0)],
            9,
        )
    world_sid = add(
        "world",
        1,
        "FIXTURE-SP4 World update 1",
        _WORLD_BODIES[0],
        [(world_id, 0)],
        6,
    )

    async def ingest_fn():
        return pool, tags

    # ── Seed two users (auth → users via handle_new_user trigger) + profiles ──
    strict_email = f"sp4-strict-{runtag}@fixture.news20.test"
    broad_email = f"sp4-broad-{runtag}@fixture.news20.test"
    strict_user = supabase.auth.admin.create_user(
        {"email": strict_email, "email_confirm": True}
    ).user
    broad_user = supabase.auth.admin.create_user(
        {"email": broad_email, "email_confirm": True}
    ).user
    strict_uid, broad_uid = str(strict_user.id), str(broad_user.id)

    supabase.table("user_interest_profile").insert(
        [
            {
                "profile_user_id": strict_uid,
                "profile_interest_id": cricket_india_id,
                "profile_weight": 1.0,
                "profile_source": "typed",
                "profile_is_strict": True,
            },
            {
                "profile_user_id": broad_uid,
                "profile_interest_id": sport_id,
                "profile_weight": 1.0,
                "profile_source": "typed",
                "profile_is_strict": False,
            },
        ]
    ).execute()
    print(f"strict user = {strict_uid} (follows sport.cricket.india, STRICT)")
    print(f"broad  user = {broad_uid} (follows sport)")

    # ── Exploration: the broad user gets a world candidate; the strict user none.
    exploration_by_user = {
        broad_uid: {
            world_id: [
                ScoredCandidate(
                    story_id=world_sid,
                    matched_interest_id=world_id,
                    score=0.6,
                    affinity=0.5,
                    depth_match=1.0,
                    importance=0.5,
                    freshness=1.0,
                    fallback_depth=0,
                )
            ]
        }
    }

    # ── PRE-FLIGHT (cheap): the loader must build 2 inputs BEFORE we pay to
    # produce. A glue bug here fails fast, before any paid render. ─────────────
    preflight = load_active_user_inputs(supabase, target, exploration_by_user)
    pf_users = {i.active_user_id for i in preflight}
    if not {strict_uid, broad_uid} <= pf_users:
        print(f"FAIL pre-flight: loader returned {pf_users}, expected both users.")
        return 1
    print(f"pre-flight loader OK: {len(preflight)} active user inputs built.")

    # ── THE PAID RUN ──────────────────────────────────────────────────────────
    print(f"\nproducing {len(pool)} fixture digests (PAID) + allocating…")
    result = await run_daily_pipeline(
        target_date=target,
        supabase_client=supabase,
        llm_client=llm_client,
        tts_client=tts_client,
        ingest_fn=ingest_fn,
        interest_nodes=interest_nodes,
        poster_genai_client=poster_client,
        exploration_by_user=exploration_by_user,
    )

    print(
        f"\ncandidate={result.candidate_story_count} "
        f"produced={result.produced_story_count} "
        f"skipped_by_gate={result.skipped_by_gate_count} "
        f"feeds_written={result.feeds.feeds_written if result.feeds else 0}"
    )

    # ── READBACK + DoD ASSERTIONS ─────────────────────────────────────────────
    def read_feed(uid: str) -> list[dict]:
        return (
            supabase.table("daily_feeds")
            .select(
                "feed_position,feed_story_id,feed_matched_interest_id,feed_slot_kind"
            )
            .eq("feed_user_id", uid)
            .eq("feed_date", target.isoformat())
            .order("feed_position")
            .execute()
            .data
            or []
        )

    strict_feed = read_feed(strict_uid)
    broad_feed = read_feed(broad_uid)
    strict_story_ids = [r["feed_story_id"] for r in strict_feed]
    broad_story_ids = [r["feed_story_id"] for r in broad_feed]

    def contiguous(feed: list[dict]) -> bool:
        return [r["feed_position"] for r in feed] == list(range(1, len(feed) + 1))

    checks: list[tuple[str, bool, str]] = []
    # a. floor of produced digests
    checks.append(
        (
            "≥10 produced digests",
            result.produced_story_count >= 10,
            f"produced={result.produced_story_count}",
        )
    )
    # b. two distinct, ordered feeds
    checks.append(
        ("strict feed non-empty", len(strict_feed) > 0, f"{len(strict_feed)} rows")
    )
    checks.append(
        ("broad feed non-empty", len(broad_feed) > 0, f"{len(broad_feed)} rows")
    )
    checks.append(("strict feed ordered 01..N", contiguous(strict_feed), ""))
    checks.append(("broad feed ordered 01..N", contiguous(broad_feed), ""))
    checks.append(
        (
            "feeds are DISTINCT",
            set(strict_story_ids) != set(broad_story_ids),
            f"{len(set(strict_story_ids) ^ set(broad_story_ids))} differing",
        )
    )
    # c. strict = cricket.india leaf only, no fallback, no exploration
    strict_only_cricket = set(strict_story_ids) <= cricket_ids
    checks.append(
        (
            "strict user: only cricket.india leaf stories (no upward fallback)",
            strict_only_cricket,
            f"non-cricket leak: {set(strict_story_ids) - cricket_ids}",
        )
    )
    strict_no_explore = all(r["feed_slot_kind"] != "exploration" for r in strict_feed)
    checks.append(("strict user: NO exploration rows", strict_no_explore, ""))
    # d. niche reaches broad + broad has exploration
    niche_reached = bool(set(broad_story_ids) & arsenal_ids)
    checks.append(
        (
            "niche soccer story reaches BROAD sport follower (grandparent tag)",
            niche_reached,
            f"arsenal in broad: {sorted(set(broad_story_ids) & arsenal_ids)}",
        )
    )
    broad_has_explore = any(r["feed_slot_kind"] == "exploration" for r in broad_feed)
    checks.append(
        (
            "broad user: HAS an exploration row (world)",
            broad_has_explore and world_sid in broad_story_ids,
            "",
        )
    )

    print("\n--- DoD CHECKS ---")
    all_ok = True
    for label, ok, detail in checks:
        all_ok = all_ok and ok
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {label}"
            + (f"  ({detail})" if detail else "")
        )

    print("\n--- FEEDS (auditable) ---")
    print(
        f"strict ({strict_uid}): {[(r['feed_position'], r['feed_slot_kind']) for r in strict_feed]}"
    )
    print(
        f"broad  ({broad_uid}): positions={len(broad_feed)} "
        f"kinds={sorted({r['feed_slot_kind'] for r in broad_feed})}"
    )
    print(
        f"\ncleanup: stories prefix 'FIXTURE-SP4-{runtag}-', users {strict_uid} / {broad_uid}"
    )

    return 0 if all_ok else 1


def main() -> None:
    if os.environ.get("RUN_LIVE_E2E") != "1":
        print(
            "Refusing to run: this makes PAID Gemini calls + REAL Supabase writes.\n"
            "Set RUN_LIVE_E2E=1 to run the SP4 live batch e2e."
        )
        sys.exit(2)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
