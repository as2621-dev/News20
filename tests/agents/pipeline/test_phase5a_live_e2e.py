"""Live end-to-end check for phase-5a (Build-your-30 + entity-aware ranking).

This is the ONE test in the pipeline suite that talks to the LIVE Supabase. It is
SKIPPED by default — it runs only when the Supabase service-role credentials are
present (locally via ``.env``; absent in network-free CI). The offline sim
(``test_ranking_simulation.py::test_entity_follow_lifts_story_above_twin_within_category``
+ ``::test_entity_scenario_honors_category_budgets_and_sequence``) is the CI-safe,
deterministic proof of the SAME invariants — this test confirms they also hold when
the follows + allocation are hydrated from the real DB through the real loaders.

What it proves end-to-end against the live DB (phase-5a SP4 (a)):

  1. A disposable test user is created (``auth.admin.create_user``; the ``users``
     mirror row is auto-created by the auth trigger).
  2. Its ``user_feed_allocation`` (the DoD budget) + a custom-source Nvidia
     ``user_entity_follows`` are seeded via the service-role client.
  3. The REAL loaders (``_load_followed_entities`` ⋈ ``entities``,
     ``_load_category_allocation``) hydrate those rows from the live DB.
  4. ``assemble_user_feed`` runs over a deterministic in-memory story pool (the
     twin-Nvidia scenario) so the entity ordering is provable.
  5. Asserts: the per-category budgets + sequence are honored, the source budgets
     (youtube/x) roll into the topics so the feed totals 30, and the Nvidia story
     outranks its non-followed twin WITHIN markets.
  6. The disposable user is deleted — the FK cascade removes the seeded allocation
     + follow rows, leaving the live DB with only schema + the 248-row entity seed.

DIVERGENCE (surfaced, Rule 7/12): the feed is asserted on the ``assemble_user_feed``
output, NOT persisted to ``daily_feeds``. Writing ``daily_feeds`` would require
seeding real ``stories`` + ``interests`` rows (FK-constrained) and risk polluting
the shared live story pool; the ``write_daily_feed`` persistence path is already
covered by the produce-once idempotency tests in ``test_feed_assembly.py`` against a
mocked client. The load-bearing phase-5a behavior under test here is the live
allocation + entity-aware ranking, which this exercises against the real DB.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

# Reason: load .env so the live creds are visible to the skip gate when present.
# In network-free CI there is no .env file, so the creds stay absent → the test
# SKIPS (the suite stays network-free). Locally .env supplies them → it runs.
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
if _ENV_PATH.exists():
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)

_REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
_HAS_LIVE_CREDS = all(os.environ.get(name) for name in _REQUIRED_ENV)

pytestmark = pytest.mark.skipif(
    not _HAS_LIVE_CREDS,
    reason="live Supabase creds (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY) absent; "
    "the offline sim is the CI-safe proof",
)

# The live Nvidia entity (migration 0007 seed) — a 'company' with ticker 'NVDA'.
# One of three Nvidia rows (multi-path); the loader hydrates whichever path is
# followed. Used as a custom-source follow for the strongest EntityBonus.
_NVIDIA_ENTITY_ID = "ai/ai-hardware-compute/companies-topics/nvidia"

# The DoD allocation (phase-5a SP4): topic + source budgets sum to 30, so the feed
# must be 30 slots with the 9 source slots (youtube 6 + x 3) rolled into topics.
_DOD_ALLOCATION: tuple[tuple[str, int, int], ...] = (
    ("breaking", 2, 0),
    ("world_politics", 4, 1),
    ("tech_science", 5, 2),
    ("markets", 4, 3),
    ("sport", 3, 4),
    ("culture", 3, 5),
    ("youtube", 6, 6),
    ("x", 3, 7),
)


def test_live_allocator_honors_budgets_and_lifts_followed_entity() -> None:
    """Live e2e: hydrate a seeded user's allocation + Nvidia follow from the real
    DB, run the allocator, assert the budget + entity invariants, then clean up.

    WHY (Rule 9): this fails if the live loaders stop hydrating the phase-5a
    contracts (``user_feed_allocation`` / ``user_entity_follows ⋈ entities``), if
    the custom-source weighting is not applied, if the allocator ignores the
    per-category budgets, or if the EntityBonus no longer lifts the Nvidia story
    above its twin. It mirrors the offline sim so a live regression is caught even
    when the deterministic sim still passes (e.g. a loader/schema drift)."""
    from supabase import create_client

    from agents.pipeline.categories import category_for_slug
    from agents.pipeline.daily_batch import (
        _load_category_allocation,
        _load_followed_entities,
    )
    from agents.pipeline.feed_assembly import assemble_user_feed
    from agents.pipeline.sim.world import (
        SIM_NOW,
        build_entity_boost_scenario,
        build_taxonomy,
    )

    supabase_client = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    disposable_email = f"phase5a-e2e-{uuid.uuid4().hex[:12]}@example.invalid"
    created = supabase_client.auth.admin.create_user(
        {
            "email": disposable_email,
            "password": uuid.uuid4().hex,
            "email_confirm": True,
        }
    )
    test_user_id = created.user.id

    try:
        # The ``users`` mirror row is auto-created by the auth trigger; assert it.
        users_row = (
            supabase_client.table("users")
            .select("user_id")
            .eq("user_id", test_user_id)
            .execute()
        )
        assert users_row.data, "auth trigger must auto-create the users mirror row"

        # Seed the DoD allocation + a custom-source Nvidia follow.
        supabase_client.table("user_feed_allocation").insert(
            [
                {
                    "follow_user_id": test_user_id,
                    "allocation_category": category,
                    "allocation_slot_count": slot_count,
                    "allocation_sort_order": sort_order,
                }
                for category, slot_count, sort_order in _DOD_ALLOCATION
            ]
        ).execute()
        supabase_client.table("user_entity_follows").insert(
            {
                "follow_user_id": test_user_id,
                "entity_id": _NVIDIA_ENTITY_ID,
                "follow_path": ["ai", "ai-hardware-compute", "nvidia"],
                "follow_source": "custom",
                "follow_weight": 1.0,
            }
        ).execute()

        # ── Hydrate via the REAL loaders (the live allocation + entity contracts) ──
        followed_entities = _load_followed_entities(
            supabase_client, [test_user_id]
        ).get(test_user_id, [])
        category_allocation = _load_category_allocation(
            supabase_client, [test_user_id]
        ).get(test_user_id, [])

        assert len(category_allocation) == len(_DOD_ALLOCATION), (
            "the live loader must hydrate every seeded allocation row"
        )
        assert len(followed_entities) == 1, "the Nvidia follow must hydrate"
        nvidia_entity = followed_entities[0]
        assert nvidia_entity.entity_label == "Nvidia"
        assert nvidia_entity.entity_kind == "company"
        # custom source → FOLLOW_SOURCE_WEIGHT['custom'] (3.0) applied by the loader.
        assert nvidia_entity.follow_weight == pytest.approx(3.0), (
            "the loader must apply the custom-source weighting (custom > more > seed)"
        )

        # ── Run the allocator path over a deterministic twin-Nvidia story pool ──
        interest_nodes = build_taxonomy()
        stories, story_interest_tags, scenario_profile = build_entity_boost_scenario(
            interest_nodes
        )
        slots = assemble_user_feed(
            profile_interests=scenario_profile.interests,
            stories=stories,
            story_interest_tags=story_interest_tags,
            interest_nodes=interest_nodes,
            followed_entities=followed_entities,
            category_allocation=category_allocation,
            now_utc=SIM_NOW,
        )

        # ── Budget invariants (per-category counts + sequence + total 30) ──
        story_ids = [slot.feed_story_id for slot in slots]
        assert len(slots) == 30, "source budgets must roll into topics → feed totals 30"
        assert len(story_ids) == len(set(story_ids)), "no story may appear twice"

        breaking_count = sum(1 for s in slots if s.feed_slot_kind == "breaking")
        assert breaking_count == 2, "breaking is user-budgeted to 2 (not the default 4)"

        markets_slots = [
            s
            for s in slots
            if s.feed_slot_kind != "breaking"
            and category_for_slug(s.feed_matched_interest_id) == "markets"
        ]
        assert len(markets_slots) == 4, "markets must hold exactly its 4-slot budget"

        # Sequence: breaking first, then topics in allocation_sort_order.
        category_sequence: list[str] = []
        for slot in slots:
            category = (
                "breaking"
                if slot.feed_slot_kind == "breaking"
                else category_for_slug(slot.feed_matched_interest_id)
            )
            if category not in category_sequence:
                category_sequence.append(category)
        assert category_sequence[0] == "breaking"
        topic_order = [c for c in category_sequence if c != "breaking"]
        expected_order = ["world_politics", "tech_science", "markets", "sport"]
        assert topic_order == [c for c in expected_order if c in topic_order]

        # ── Entity invariant (the Nvidia story outranks its twin WITHIN markets) ──
        by_id = {s.feed_story_id: s for s in slots}
        assert "ent-twin-nvidia" in by_id, "the Nvidia story must be placed"
        assert "ent-twin-plain" in by_id, "the non-followed twin must be placed"
        nvidia_slot, twin_slot = by_id["ent-twin-nvidia"], by_id["ent-twin-plain"]
        assert category_for_slug(nvidia_slot.feed_matched_interest_id) == "markets"
        assert category_for_slug(twin_slot.feed_matched_interest_id) == "markets"
        assert nvidia_slot.feed_score > twin_slot.feed_score, (
            "the live-hydrated Nvidia follow must lift its story's Score above the twin"
        )
        assert nvidia_slot.feed_position < twin_slot.feed_position, (
            "the Nvidia story must be ordered above its non-followed twin"
        )
    finally:
        # ── Clean up: delete the disposable user; the FK cascade removes its
        # allocation + follow rows. Leaves the live DB with schema + entity seed. ──
        supabase_client.auth.admin.delete_user(test_user_id)

        leftover_allocation = (
            supabase_client.table("user_feed_allocation")
            .select("follow_user_id", count="exact")
            .eq("follow_user_id", test_user_id)
            .execute()
        )
        leftover_follows = (
            supabase_client.table("user_entity_follows")
            .select("follow_user_id", count="exact")
            .eq("follow_user_id", test_user_id)
            .execute()
        )
        leftover_users = (
            supabase_client.table("users")
            .select("user_id", count="exact")
            .eq("user_id", test_user_id)
            .execute()
        )
        assert leftover_allocation.count == 0, "seeded allocation rows must be cascaded"
        assert leftover_follows.count == 0, "seeded follow rows must be cascaded"
        assert leftover_users.count == 0, "the disposable user must be removed"
