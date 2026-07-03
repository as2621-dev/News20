"""Unit tests for the persona seed script (slice #3).

No Supabase, no network: the client is faked at the boundary (auth.admin, rpc,
table().upsert()) so we assert the mint→profile wiring — one RPC mint per accepted
micro-interest, one batched profile upsert, correct depth-weight / strict / label,
and that a rejected (off-shape) item is surfaced NOT minted.

WHY these matter (Rule 9 — encode intent): the seed is the interview's stand-in, so
its slug/anchor/weight conventions MUST match the browser persister exactly or the
seeded nodes diverge from interview-minted ones (the whole point of the RPC path is
convergence). The rejection backstop encodes "a privileged mint never trusts its
input"; the same-leaf dedup encodes the Postgres "ON CONFLICT once per row" rule.

    >>> pytest tests/scripts/test_seed_personas.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.seed_personas import (
    PERSONA_SPECS,
    Persona,
    PersonaMicroInterest,
    build_search_query,
    distinct_non_empty,
    micro_interest_rejection_reason,
    resolve_profile_weight,
    seed_persona,
)


def _valid(slug: str, *, strict: bool = False) -> PersonaMicroInterest:
    """A minimal VALID micro-interest anchored at ``slug`` (≥2 anchor terms)."""
    return PersonaMicroInterest(
        canonical_slug=slug,
        display_label="Label",
        search_anchor_terms=["alpha term", "beta term"],
        strict=strict,
    )


class TestPureHelpers:
    """distinct_non_empty / build_search_query / weight — the interview twins."""

    def test_distinct_non_empty_strips_dedups_case_insensitively(self) -> None:
        assert distinct_non_empty([" TSMC ", "tsmc", "", "GPU"]) == ["TSMC", "GPU"]

    def test_build_search_query_comma_joins_distinct_terms(self) -> None:
        assert build_search_query(["TSMC", "tsmc", "chip foundry"]) == (
            "TSMC, chip foundry"
        )

    def test_profile_weight_deepens_with_depth(self) -> None:
        assert resolve_profile_weight(0) == 1.0
        assert resolve_profile_weight(2) == 2.0
        assert resolve_profile_weight(3) == 2.5

    def test_profile_weight_unknown_depth_falls_back(self) -> None:
        assert resolve_profile_weight(9) == 1.0


class TestRejectionReason:
    """The validation backstop — mirror of interviewProfile.rejectionReason."""

    def test_valid_micro_interest_passes(self) -> None:
        assert micro_interest_rejection_reason(_valid("sport.cricket.ipl")) is None

    def test_slug_not_root_anchored_rejected(self) -> None:
        assert (
            micro_interest_rejection_reason(_valid("notaroot.thing"))
            == "slug_not_root_anchored"
        )

    def test_slug_too_deep_rejected(self) -> None:
        assert (
            micro_interest_rejection_reason(_valid("sport.a.b.c.d"))
            == "slug_too_deep"
        )

    def test_malformed_segment_rejected(self) -> None:
        assert (
            micro_interest_rejection_reason(_valid("sport.Cricket_IPL"))
            == "malformed_slug_segment"
        )

    def test_under_two_anchor_terms_rejected(self) -> None:
        item = PersonaMicroInterest(
            canonical_slug="sport.cricket",
            display_label="Cricket",
            search_anchor_terms=["only-one", "only-one"],  # dedups to 1
        )
        assert micro_interest_rejection_reason(item) == "under_two_anchor_terms"

    def test_empty_display_label_rejected(self) -> None:
        item = PersonaMicroInterest(
            canonical_slug="sport.cricket",
            display_label="   ",
            search_anchor_terms=["a term", "b term"],
        )
        assert micro_interest_rejection_reason(item) == "empty_display_label"


class TestPersonaSpecsAreValid:
    """The 3 shipped personas must all be spec-valid (a bad edit fails loud here)."""

    def test_three_personas_defined(self) -> None:
        assert [p.persona_key for p in PERSONA_SPECS] == ["founder", "cricket", "chip"]

    def test_every_shipped_micro_interest_is_valid(self) -> None:
        for persona in PERSONA_SPECS:
            for item in persona.micro_interests:
                assert micro_interest_rejection_reason(item) is None, (
                    f"{persona.persona_key}:{item.canonical_slug}"
                )

    def test_cricket_ipl_is_strict(self) -> None:
        cricket = next(p for p in PERSONA_SPECS if p.persona_key == "cricket")
        ipl = next(
            i for i in cricket.micro_interests if i.canonical_slug == "sport.cricket.ipl"
        )
        assert ipl.strict is True


def _fake_supabase(mint_ids: list[str]) -> MagicMock:
    """A fake Supabase client: create_user → uid, rpc mint → next id, upsert captured.

    ``mint_ids`` is the sequence of leaf ids the mint RPC returns (one per call).
    ``client.captured_upsert`` holds the rows passed to the profile upsert.
    """
    client = MagicMock()
    client.auth.admin.create_user.return_value.user.id = "user-abc"

    ids = iter(mint_ids)

    def _rpc(_name, _params):
        response = MagicMock()
        response.execute.return_value.data = next(ids)
        return response

    client.rpc.side_effect = _rpc

    def _table(_name):
        table = MagicMock()

        def _upsert(rows, on_conflict=None):
            client.captured_upsert = {"rows": rows, "on_conflict": on_conflict}
            return MagicMock()

        table.upsert.side_effect = _upsert
        return table

    client.table.side_effect = _table
    return client


class TestSeedPersona:
    """seed_persona — the mint→profile wiring, with the client faked at the boundary."""

    def test_happy_path_mints_each_and_upserts_one_batch(self) -> None:
        """3 valid interests → 3 mint RPC calls + ONE profile upsert of 3 rows carrying
        the right weight (by depth), strict flag, source, and display label."""
        persona = Persona(
            persona_key="p",
            persona_email="p@news20.seed",
            micro_interests=[
                PersonaMicroInterest(
                    canonical_slug="sport.cricket.ipl",
                    display_label="IPL",
                    search_anchor_terms=["Indian Premier League", "IPL cricket"],
                    strict=True,
                ),
                PersonaMicroInterest(
                    canonical_slug="business.venture-capital",
                    display_label="VC",
                    search_anchor_terms=["venture capital", "startup funding"],
                ),
            ],
        )
        client = _fake_supabase(mint_ids=["leaf-ipl", "leaf-vc"])

        summary = seed_persona(client, persona)

        assert client.rpc.call_count == 2  # one mint per accepted interest
        assert summary["minted"] == 2
        assert summary["rejected"] == []
        rows = {r["profile_interest_id"]: r for r in client.captured_upsert["rows"]}
        assert client.captured_upsert["on_conflict"] == (
            "profile_user_id,profile_interest_id"
        )
        # depth-2 IPL → weight 2.0, strict; depth-1 VC → weight 1.5, not strict.
        assert rows["leaf-ipl"]["profile_weight"] == 2.0
        assert rows["leaf-ipl"]["profile_is_strict"] is True
        assert rows["leaf-ipl"]["profile_display_label"] == "IPL"
        assert rows["leaf-vc"]["profile_weight"] == 1.5
        assert rows["leaf-vc"]["profile_is_strict"] is False
        assert all(r["profile_source"] == "typed" for r in rows.values())
        assert all(r["profile_user_id"] == "user-abc" for r in rows.values())

    def test_rejected_item_is_surfaced_not_minted(self) -> None:
        """An off-shape micro-interest is reported in ``rejected`` and never minted;
        the valid sibling still persists (reject the bad one, keep the rest)."""
        persona = Persona(
            persona_key="p",
            persona_email="p@news20.seed",
            micro_interests=[
                _valid("sport.cricket.ipl"),
                _valid("notaroot.thing"),  # rejected: not root-anchored
            ],
        )
        client = _fake_supabase(mint_ids=["leaf-ipl"])

        summary = seed_persona(client, persona)

        assert client.rpc.call_count == 1  # only the valid one minted
        assert summary["minted"] == 1
        assert summary["rejected"] == [
            {"canonical_slug": "notaroot.thing", "reason": "slug_not_root_anchored"}
        ]

    def test_duplicate_leaf_id_collapses_to_one_profile_row(self) -> None:
        """Two interests minting to the SAME leaf id yield ONE profile row (Postgres
        forbids ON CONFLICT touching a row twice per upsert)."""
        persona = Persona(
            persona_key="p",
            persona_email="p@news20.seed",
            micro_interests=[_valid("sport.cricket"), _valid("sport.cricket.ipl")],
        )
        client = _fake_supabase(mint_ids=["same-leaf", "same-leaf"])

        summary = seed_persona(client, persona)

        assert summary["minted"] == 1
        assert len(client.captured_upsert["rows"]) == 1

    def test_mint_returning_no_id_raises_loud(self) -> None:
        """A mint RPC that returns no id is a HARD failure (surface it — Rule 12)."""
        persona = Persona(
            persona_key="p",
            persona_email="p@news20.seed",
            micro_interests=[_valid("sport.cricket.ipl")],
        )
        client = _fake_supabase(mint_ids=[None])  # type: ignore[list-item]

        with pytest.raises(RuntimeError, match="mint_interest_ladder"):
            seed_persona(client, persona)
