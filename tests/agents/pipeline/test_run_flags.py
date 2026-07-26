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
