"""On-demand FREE per-user feed allocator for the 4 E2E test users.

Freshly-seeded test users have no ``daily_feeds`` rows (the Trigger.dev cron
writes those). This script reuses the REAL pipeline ranking/allocation code
(``assemble_user_feed`` + ``write_daily_feed``) to write TODAY's ``daily_feeds``
for ONLY the test users listed in the seeded-users file, from the
already-produced live story pool (stories ⋈ current digests ⋈ story_interests).
No LLM/TTS/poster calls — stages A–C never run here.

Run:
    .venv/bin/python scripts/e2e/allocate_test_feeds.py .agents/e2e/state/test-users.json

Output: structured JSON lines to stdout — one ``user_feed_allocated`` line per
user (ordered story_id / headline / category / score), then a final
``allocate_test_feeds_completed`` summary. FAILS LOUD (exit 1) when any two
profiles produce identical orderings (``orderings_identical``) or any user gets
zero stories (``pool_too_thin`` — AMBER: the live pool may simply not cover
their interests).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Any

# Reason: make the repo root importable when run as a bare script (mirrors
# scripts/run_live_batch.py).
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.ingestion.models import (  # noqa: E402
    CanonicalStory,
    InterestNode,
    StoryInterestTag,
)
from agents.pipeline.categories import category_for_slug  # noqa: E402
from agents.pipeline.daily_batch import load_active_user_inputs  # noqa: E402
from agents.pipeline.feed_assembly import (  # noqa: E402
    FEED_SLOT_BUDGET,
    SLOT_KIND_SOURCE,
    AllocatedSlot,
    assemble_user_feed,
    write_daily_feed,
)
from agents.pipeline.orchestrator import ActiveUserFeedInputs  # noqa: E402
from scripts.run_live_batch import _INTEREST_COLS, _apply_dns_pin, _node  # noqa: E402

# Reason: PostgREST ``.in_()`` filters ride the URL; chunk id lists so a large
# produced pool can never overflow the URL length limit.
_IN_CHUNK_SIZE = 150


def _log(event: str, **fields: Any) -> None:
    """Emit one structured JSON log line to stdout (CLAUDE.md logging mandate)."""
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def _chunked(items: list[str], size: int = _IN_CHUNK_SIZE) -> list[list[str]]:
    """Split ``items`` into chunks of at most ``size`` for ``.in_()`` filters."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _create_supabase_client() -> Any:
    """Load repo-root .env and build the service-role client (values never printed).

    Mirrors ``scripts/run_live_batch.py``: ``SUPABASE_URL`` with a
    ``NEXT_PUBLIC_SUPABASE_URL`` fallback + ``SUPABASE_SERVICE_ROLE_KEY``.

    Returns:
        A service-role supabase client.

    Raises:
        SystemExit: When a required env var is missing (names only, no values).
    """
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_URL"
    )
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        _log(
            "env_missing",
            missing=[
                name
                for name, value in (
                    ("SUPABASE_URL|NEXT_PUBLIC_SUPABASE_URL", supabase_url),
                    ("SUPABASE_SERVICE_ROLE_KEY", service_role_key),
                )
                if not value
            ],
            fix_suggestion="Add the missing keys to the repo-root .env.",
        )
        raise SystemExit(1)
    return create_client(supabase_url, service_role_key)


def _load_interest_nodes(supabase_client: Any) -> dict[str, InterestNode]:
    """Load the full interests taxonomy as ``{interest_id: InterestNode}``.

    Reuses the ``_node`` row mapper + column list from ``scripts/run_live_batch.py``.

    Args:
        supabase_client: Service-role client.

    Returns:
        The taxonomy lookup the scorer/allocator consume.
    """
    rows = (
        supabase_client.table("interests").select(_INTEREST_COLS).execute().data or []
    )
    return {str(row["interest_id"]): _node(row) for row in rows}


def _load_story_pool(
    supabase_client: Any,
) -> tuple[list[CanonicalStory], list[StoryInterestTag], dict[str, str]]:
    """Load the already-produced live story pool (stories ⋈ current digests ⋈ tags).

    Reconstructs the pipeline's canonical models from the persisted rows. The
    scorer only reads ``canonical_title`` (entity match), ``story_outlet_count``
    (importance) and ``canonical_published_utc`` (freshness) — all real columns;
    URL/domain fields are required-but-unscored, so they get placeholders.

    Args:
        supabase_client: Service-role client.

    Returns:
        ``(stories, story_interest_tags, {story_id: headline})``.
    """
    digest_rows = (
        supabase_client.table("digests")
        .select("digest_story_id")
        .eq("digest_is_current", True)
        .execute()
        .data
        or []
    )
    story_ids = sorted({str(row["digest_story_id"]) for row in digest_rows})

    stories: list[CanonicalStory] = []
    headline_by_id: dict[str, str] = {}
    tags: list[StoryInterestTag] = []
    for chunk in _chunked(story_ids):
        story_rows = (
            supabase_client.table("stories")
            .select(
                "story_id,story_headline,story_first_reported_utc,"
                "story_primary_outlet_name,story_outlet_count"
            )
            .in_("story_id", chunk)
            .execute()
            .data
            or []
        )
        for row in story_rows:
            story_id = str(row["story_id"])
            headline = str(row["story_headline"])
            headline_by_id[story_id] = headline
            stories.append(
                CanonicalStory(
                    canonical_story_id=story_id,
                    canonical_title=headline,
                    # Reason: required by the model but never read by scoring;
                    # the persisted stories table carries no URL columns.
                    canonical_url=f"story://{story_id}",
                    canonical_normalized_url=f"story://{story_id}",
                    canonical_published_utc=datetime.fromisoformat(
                        str(row["story_first_reported_utc"])
                    ),
                    canonical_primary_outlet_domain=str(
                        row.get("story_primary_outlet_name") or "unknown"
                    ),
                    canonical_primary_outlet_name=row.get("story_primary_outlet_name"),
                    story_outlet_count=int(row.get("story_outlet_count") or 0),
                )
            )
        tag_rows = (
            supabase_client.table("story_interests")
            .select(
                "story_interest_story_id,story_interest_interest_id,"
                "story_interest_match_depth,story_interest_relevance"
            )
            .in_("story_interest_story_id", chunk)
            .execute()
            .data
            or []
        )
        for row in tag_rows:
            tags.append(
                StoryInterestTag(
                    story_interest_story_id=str(row["story_interest_story_id"]),
                    story_interest_interest_id=str(row["story_interest_interest_id"]),
                    story_interest_match_depth=int(row["story_interest_match_depth"]),
                    story_interest_relevance=(
                        float(row["story_interest_relevance"])
                        if row.get("story_interest_relevance") is not None
                        else None
                    ),
                )
            )
    return stories, tags, headline_by_id


def _slot_category(slot: AllocatedSlot, interest_nodes: dict[str, InterestNode]) -> str:
    """Resolve a slot's screen category (source/breaking tier, else its interest's root)."""
    if slot.feed_slot_kind == SLOT_KIND_SOURCE:
        return "source"
    if slot.feed_slot_kind == "breaking":
        return "breaking"
    node = (
        interest_nodes.get(slot.feed_matched_interest_id)
        if slot.feed_matched_interest_id
        else None
    )
    return category_for_slug(node.interest_slug) if node else "culture"


def _matching_pool_story_count(
    user_inputs: ActiveUserFeedInputs | None, tags: list[StoryInterestTag]
) -> int:
    """Count distinct pool stories tagged with ANY of the user's followed interests.

    Diagnoses a zero-story user: 0 here means the live pool simply has no current
    digests matching their interests (AMBER), not an allocator bug.
    """
    if user_inputs is None:
        return 0
    followed_ids = {p.profile_interest_id for p in user_inputs.profile_interests}
    return len(
        {
            tag.story_interest_story_id
            for tag in tags
            if tag.story_interest_interest_id in followed_ids
        }
    )


def _load_existing_source_rows(
    supabase_client: Any, user_id: str, feed_date_iso: str
) -> list[dict[str, Any]]:
    """Load a user's existing ``feed_slot_kind='source'`` daily_feeds rows for the day.

    Reason: ``assemble_user_feed`` is run here with NO ``source_stories`` argument,
    so the freshly-assembled feed has ZERO source slots. The idempotent delete then
    drops the user's whole day — silently evicting any followed-source reels produced
    by ``produce_source_reels.py``. We capture them BEFORE the delete so they can be
    re-placed (sub-phase 3 of phase-sp2-feed-rebuild-safety).

    Args:
        supabase_client: Service-role client.
        user_id: The user whose source rows to capture.
        feed_date_iso: The ISO feed date.

    Returns:
        The user's existing source rows (``feed_story_id`` / ``feed_score`` /
        ``feed_matched_interest_id`` carried), ordered by ``feed_position``.
    """
    return (
        supabase_client.table("daily_feeds")
        .select("feed_story_id,feed_score,feed_matched_interest_id,feed_position")
        .eq("feed_user_id", user_id)
        .eq("feed_date", feed_date_iso)
        .eq("feed_slot_kind", SLOT_KIND_SOURCE)
        .order("feed_position")
        .execute()
        .data
        or []
    )


def _replace_source_slots(
    slots: list[AllocatedSlot], existing_source_rows: list[dict[str, Any]]
) -> list[AllocatedSlot]:
    """Re-place carried source rows into the assembled feed, guaranteeing survival.

    The freshly-assembled ``slots`` carry ZERO source slots (no ``source_stories`` is
    passed to ``assemble_user_feed`` here). The user's prior source reels were part of
    their original 30, so we re-place them WITHOUT overshooting the budget: carried
    source rows are placed FIRST (guaranteed to survive, up to :data:`FEED_SLOT_BUDGET`)
    and the assembled topic slots fill the REMAINING budget — the lowest-priority topic
    slots are trimmed from the tail only if the combined count would exceed 30. Source
    rows are deduped on ``feed_story_id`` against the topic slots (so the
    ``uq_daily_feed_story`` constraint can't trip) and against each other. Positions are
    reassigned 1..len so the ``uq_daily_feed_position`` constraint holds. Source rows
    lead the feed in their original ``feed_position`` order; mirrors the SP1 source-row
    shape in ``produce_source_reels.py`` (``feed_slot_kind='source'``, no matched
    interest).

    Args:
        slots: The freshly-assembled topic slots (positions 1..len).
        existing_source_rows: The user's prior source rows (``feed_position`` order).

    Returns:
        The merged ordered slots (source rows first, then topic slots), repositioned
        1..len, capped at :data:`FEED_SLOT_BUDGET`. When there are no prior source
        rows this returns ``slots`` unchanged.
    """
    if not existing_source_rows:
        return slots

    source_slots: list[AllocatedSlot] = []
    seen_source_ids: set[str] = set()
    for row in existing_source_rows:
        if len(source_slots) >= FEED_SLOT_BUDGET:
            break
        story_id = str(row["feed_story_id"])
        if story_id in seen_source_ids:
            continue
        seen_source_ids.add(story_id)
        source_slots.append(
            AllocatedSlot(
                feed_story_id=story_id,
                feed_position=len(source_slots) + 1,  # repositioned below
                feed_score=float(row.get("feed_score") or 0.0),
                feed_matched_interest_id=None,
                feed_slot_kind=SLOT_KIND_SOURCE,
            )
        )

    # Reason: source rows are guaranteed-preserved; topic slots fill the remaining
    # budget. A topic slot whose story id collides with a carried source row is
    # dropped (uq_daily_feed_story). Trim topic tail so total never exceeds 30.
    topic_capacity = max(FEED_SLOT_BUDGET - len(source_slots), 0)
    topic_slots = [s for s in slots if s.feed_story_id not in seen_source_ids][
        :topic_capacity
    ]

    merged = source_slots + topic_slots
    return [
        slot.model_copy(update={"feed_position": position})
        for position, slot in enumerate(merged, start=1)
    ]


def main() -> int:
    """Allocate + write today's daily_feeds for the seeded E2E test users.

    Returns:
        0 on success; 1 on pool_too_thin (a user got zero stories — AMBER) or
        orderings_identical (personalization not differentiating) or bad input.
    """
    if len(sys.argv) != 2:
        _log(
            "usage_error",
            fix_suggestion=".venv/bin/python scripts/e2e/allocate_test_feeds.py "
            ".agents/e2e/state/test-users.json",
        )
        return 1
    users_path = sys.argv[1]
    with open(users_path, encoding="utf-8") as fh:
        test_users: list[dict[str, str]] = json.load(fh)

    # Reason: supabase-py's httpx logs every request at INFO; keep stdout to OUR
    # structured events only (the orchestrator parses these lines).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _apply_dns_pin()
    supabase_client = _create_supabase_client()
    target_date = date.today()
    now_utc = datetime.now(timezone.utc)

    interest_nodes = _load_interest_nodes(supabase_client)
    stories, story_interest_tags, headline_by_id = _load_story_pool(supabase_client)
    _log(
        "pool_loaded",
        feed_date=target_date.isoformat(),
        pool_story_count=len(stories),
        pool_tag_count=len(story_interest_tags),
        taxonomy_interest_count=len(interest_nodes),
    )

    # Reuse the REAL batch loader (profiles + entities + allocations + §3.8 prior
    # feeds, all batched), then filter to ONLY the seeded test users.
    inputs_by_user_id = {
        inputs.active_user_id: inputs
        for inputs in load_active_user_inputs(supabase_client, target_date)
    }

    per_user_counts: dict[str, int] = {}
    orderings: dict[str, tuple[str, ...]] = {}
    zero_story_users: list[dict[str, Any]] = []

    for test_user in test_users:
        profile_name = test_user["profile_name"]
        user_id = test_user["user_id"]
        user_inputs = inputs_by_user_id.get(user_id)

        if user_inputs is None:
            # Reason: requirement 8 — a seeded user with NO user_interest_profile
            # rows (browser driver not run yet) must not crash the script; they
            # legitimately produce an empty feed.
            slots: list[AllocatedSlot] = []
        else:
            slots = assemble_user_feed(
                profile_interests=user_inputs.profile_interests,
                stories=stories,
                story_interest_tags=story_interest_tags,
                interest_nodes=interest_nodes,
                followed_entities=user_inputs.followed_entities,
                category_allocation=user_inputs.category_allocation,
                prior_feed_story_ids=set(user_inputs.prior_feed_story_ids),
                now_utc=now_utc,
            )

        # Source-aware re-placement (phase-sp2 SP3): assemble_user_feed is run here
        # WITHOUT source_stories, so the fresh feed has zero source slots. Capture the
        # user's existing followed-source reels BEFORE the delete and re-place them
        # (leading the feed, within the 30-budget) so the idempotent delete can never
        # silently evict a previously-produced source reel (the Nitish-eviction bug).
        existing_source_rows = _load_existing_source_rows(
            supabase_client, user_id, target_date.isoformat()
        )
        slots = _replace_source_slots(slots, existing_source_rows)
        carried_source_count = sum(
            1 for slot in slots if slot.feed_slot_kind == SLOT_KIND_SOURCE
        )
        if carried_source_count:
            _log(
                "source_rows_replaced",
                profile_name=profile_name,
                user_id=user_id,
                existing_source_row_count=len(existing_source_rows),
                carried_source_count=carried_source_count,
            )

        # Idempotent re-run: clear THIS user's rows for today, then write fresh
        # (write_daily_feed's produce-once gate would otherwise skip the rewrite).
        supabase_client.table("daily_feeds").delete().eq("feed_user_id", user_id).eq(
            "feed_date", target_date.isoformat()
        ).execute()
        write_result = write_daily_feed(
            supabase_client=supabase_client,
            feed_user_id=user_id,
            feed_date=target_date,
            slots=slots,
        )

        ordered_stories = [
            {
                "feed_position": slot.feed_position,
                "story_id": slot.feed_story_id,
                "headline": headline_by_id.get(slot.feed_story_id, ""),
                "category": _slot_category(slot, interest_nodes),
                "feed_score": slot.feed_score,
            }
            for slot in slots
        ]
        _log(
            "user_feed_allocated",
            profile_name=profile_name,
            user_id=user_id,
            story_count=len(slots),
            slots_written=write_result.slots_written,
            stories=ordered_stories,
        )

        per_user_counts[profile_name] = len(slots)
        orderings[profile_name] = tuple(slot.feed_story_id for slot in slots)
        if not slots:
            zero_story_users.append(
                {
                    "profile_name": profile_name,
                    "user_id": user_id,
                    "followed_interest_count": (
                        len(user_inputs.profile_interests) if user_inputs else 0
                    ),
                    "matching_pool_story_count": _matching_pool_story_count(
                        user_inputs, story_interest_tags
                    ),
                }
            )

    identical_pairs = [
        [name_a, name_b]
        for index, (name_a, order_a) in enumerate(orderings.items())
        for name_b, order_b in list(orderings.items())[index + 1 :]
        if order_a == order_b
    ]
    orderings_distinct = not identical_pairs

    _log(
        "allocate_test_feeds_completed",
        feed_date=target_date.isoformat(),
        per_user_counts=per_user_counts,
        orderings_distinct=orderings_distinct,
    )

    if zero_story_users:
        _log(
            "pool_too_thin",
            zero_story_users=zero_story_users,
            fix_suggestion="A zero-story user with matching_pool_story_count=0 means "
            "the live pool has no current digests for their interests (AMBER); "
            "run the browser driver / live batch first otherwise.",
        )
        return 1
    if not orderings_distinct:
        _log(
            "orderings_identical",
            identical_pairs=identical_pairs,
            fix_suggestion="Personalization is not differentiating these profiles; "
            "check user_interest_profile / user_feed_allocation rows differ.",
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
