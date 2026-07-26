"""Tests for the shared spend-safety run flags (issues #66, #65).

WHY (Rule 9): these two flags decide whether a production run SPENDS. The whole
point of putting them in one module is that the local script and the deployed
worker cannot drift — so the tests encode the *defaults* (unset must be the SAFE
value on both entry points) and the fact that only an explicit falsy spelling can
turn a safety default off. A test that only checked ``"1" -> True`` would still
pass if someone flipped the default to unsafe.
"""

from __future__ import annotations

import pytest

from agents.pipeline.run_flags import (
    RUN_STAGE_REELS,
    RUN_STAGE_SCRIPTS,
    RUN_STAGE_SHORTLIST,
    resolve_run_stage,
    semantic_relevance_key_enabled,
    shortlist_only_enabled,
)


class TestShortlistOnlyEnabled:
    def test_unset_defaults_to_halt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Founder rule 2026-07-19: production is OPT-IN. An entry point that never
        sets the var must halt at the shortlist, never produce."""
        monkeypatch.delenv("SHORTLIST_ONLY", raising=False)

        assert shortlist_only_enabled() is True

    def test_explicit_zero_opts_into_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The approved path: the founder reviewed the list and set the opt-out."""
        monkeypatch.setenv("SHORTLIST_ONLY", "0")

        assert shortlist_only_enabled() is False

    def test_garbage_value_stays_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failure case: a typo must NOT silently authorize spend. Only the known
        falsy spellings opt out; anything unrecognized keeps the halt."""
        monkeypatch.setenv("SHORTLIST_ONLY", "no-really-produce")

        assert shortlist_only_enabled() is True

    @pytest.mark.parametrize("falsy_value", ["0", "false", "FALSE", "no", "off", " 0 "])
    def test_all_falsy_spellings_opt_out(
        self, monkeypatch: pytest.MonkeyPatch, falsy_value: str
    ) -> None:
        """Edge: an operator typing ``false`` on Railway means the same thing as
        ``0``. ``os.environ.get(...) == "1"`` would have produced on ``true`` and
        halted on ``false`` — exactly backwards."""
        monkeypatch.setenv("SHORTLIST_ONLY", falsy_value)

        assert shortlist_only_enabled() is False


class TestSemanticRelevanceKeyEnabled:
    def test_unset_defaults_to_armed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Issue #51's two-key lock is the quality default: an entry point that
        never sets the var must still run the semantic half."""
        monkeypatch.delenv("ENABLE_SEMANTIC_RELEVANCE_KEY", raising=False)

        assert semantic_relevance_key_enabled() is True

    def test_explicit_zero_falls_back_to_lexical_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The documented escape hatch: lexical-only admission, zero embeddings."""
        monkeypatch.setenv("ENABLE_SEMANTIC_RELEVANCE_KEY", "0")

        assert semantic_relevance_key_enabled() is False

    def test_empty_string_stays_armed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Edge: Railway renders a cleared variable as an empty string. Empty is
        'not configured', not 'disabled'."""
        monkeypatch.setenv("ENABLE_SEMANTIC_RELEVANCE_KEY", "")

        assert semantic_relevance_key_enabled() is True


class TestResolveRunStage:
    """The three-stage production ladder (#68) — where a run is allowed to stop.

    WHY (Rule 9): the founder's 2026-07-25 decision made reels a per-run ARMED
    action. These tests encode the LADDER, not the parsing: with nothing set a run
    halts at the shortlist; opting out of that lands on SCRIPTS, never on reels;
    only an explicit arm reaches the paid media stage. A test asserting
    ``PRODUCE_REELS=1 -> reels`` alone would still pass if the unset default drifted
    to reels — which is the exact failure that costs money.
    """

    @pytest.fixture(autouse=True)
    def _clear_stage_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var_name in ("SHORTLIST_ONLY", "SCRIPTS_ONLY", "PRODUCE_REELS"):
            monkeypatch.delenv(env_var_name, raising=False)

    def test_nothing_set_halts_at_the_shortlist(self) -> None:
        """Rung 1 — the untouched default spends nothing at all."""
        assert resolve_run_stage() == RUN_STAGE_SHORTLIST

    def test_shortlist_opt_out_alone_halts_at_scripts_not_reels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rung 2 — the load-bearing one. Approving the shortlist buys SCRIPTS
        (pennies), never reels ($): the media stage needs its own arm."""
        monkeypatch.setenv("SHORTLIST_ONLY", "0")

        assert resolve_run_stage() == RUN_STAGE_SCRIPTS

    def test_arming_reels_after_the_shortlist_opt_out_reaches_reels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rung 3 — the founder's explicit go for THIS run: both opt-outs present."""
        monkeypatch.setenv("SHORTLIST_ONLY", "0")
        monkeypatch.setenv("PRODUCE_REELS", "1")

        assert resolve_run_stage() == RUN_STAGE_REELS

    def test_arming_reels_alone_still_halts_at_the_shortlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Precedence: SHORTLIST_ONLY outranks the arm. A stale PRODUCE_REELS=1 left
        on a Railway dashboard must not silently re-enable spend once the shortlist
        halt is back on."""
        monkeypatch.setenv("PRODUCE_REELS", "1")

        assert resolve_run_stage() == RUN_STAGE_SHORTLIST

    def test_scripts_only_beats_the_arm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit SCRIPTS_ONLY=1 is a halt request; it outranks the arm so the
        two flags set together can never resolve to spend."""
        monkeypatch.setenv("SHORTLIST_ONLY", "0")
        monkeypatch.setenv("SCRIPTS_ONLY", "1")
        monkeypatch.setenv("PRODUCE_REELS", "1")

        assert resolve_run_stage() == RUN_STAGE_SCRIPTS

    @pytest.mark.parametrize("garbage_value", ["yes-please", "", "0", "false"])
    def test_only_an_explicit_truthy_arm_authorizes_spend(
        self, monkeypatch: pytest.MonkeyPatch, garbage_value: str
    ) -> None:
        """Failure case: the arm is OPT-IN, so anything but a recognized truthy
        spelling leaves the run at scripts. Unlike the opt-out flags, an unparsed
        value here must NOT authorize money."""
        monkeypatch.setenv("SHORTLIST_ONLY", "0")
        monkeypatch.setenv("PRODUCE_REELS", garbage_value)

        assert resolve_run_stage() == RUN_STAGE_SCRIPTS

    @pytest.mark.parametrize("truthy_value", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_recognized_truthy_spellings_arm_the_reel_stage(
        self, monkeypatch: pytest.MonkeyPatch, truthy_value: str
    ) -> None:
        """Edge: an operator typing ``true`` on Railway means the same as ``1``."""
        monkeypatch.setenv("SHORTLIST_ONLY", "0")
        monkeypatch.setenv("PRODUCE_REELS", truthy_value)

        assert resolve_run_stage() == RUN_STAGE_REELS
