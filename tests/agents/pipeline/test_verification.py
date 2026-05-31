"""Unit tests for single-source verification — the hallucination guardrail (Phase 1d SP2).

DoD (phase file SP2): ``verification`` flags an injected out-of-source claim
(a claim not supported by the source text → not grounded). The LLM is mocked at
the client boundary (no live call).

These tests encode WHY (Rule 9): the guardrail (Decision #5) exists to BLOCK a
digest whose script asserts facts the single source does not support. So we
assert that an UNSUPPORTED/CONTRADICTED verdict actually marks the report
ungrounded and (by default) halts — a regression that treated a bad claim as
publishable would fail here, not merely "a function ran".

    >>> pytest tests/agents/pipeline/test_verification.py -v
"""

from __future__ import annotations

import json

import pytest

from agents.pipeline.models import DialogueTurn, DigestScript
from agents.pipeline.stages.verification import (
    _parse_verification_response,
    run_single_source_verification,
)
from agents.shared.exceptions import PipelineStageError, VerificationHaltError


def _script(*texts_by_speaker: tuple[str, str]) -> DigestScript:
    """Build a DigestScript from ``(speaker, text)`` pairs."""
    turns = [DialogueTurn(speaker=s, text=t) for s, t in texts_by_speaker]  # type: ignore[arg-type]
    return DigestScript(digest_story_id="cand-arsenal-001", turns=turns)


def _verification_response(*claims: tuple[str, str]) -> str:
    """A verification JSON response from ``(claim_text, status)`` pairs."""
    return json.dumps(
        {
            "claims": [
                {"claim": claim, "status": status, "source_evidence": ""}
                for claim, status in claims
            ]
        }
    )


class TestFlagsOutOfSourceClaim:
    """DoD 4: an injected out-of-source claim is flagged → not grounded."""

    @pytest.mark.asyncio
    async def test_unsupported_claim_marks_ungrounded_and_halts(
        self, canonical_story, make_llm_client
    ) -> None:
        """An UNSUPPORTED claim blocks the digest (raise_on_ungrounded default True).

        The script injects a fact absent from the source ("Saka was named man of
        the match" — not in the body). The mocked verifier returns UNSUPPORTED
        for it; the guardrail MUST refuse to publish (VerificationHaltError).
        """
        script = _script(
            ("ALEX", "Arsenal beat Liverpool two to one."),
            ("JORDAN", "Right, and Saka was named man of the match."),
        )
        client = make_llm_client(
            _verification_response(
                ("Arsenal beat Liverpool two to one", "SUPPORTED"),
                ("Saka was named man of the match", "UNSUPPORTED"),
            )
        )

        with pytest.raises(VerificationHaltError) as exc_info:
            await run_single_source_verification(
                script=script, source_story=canonical_story, llm_client=client
            )
        assert exc_info.value.unsupported_count == 1

    @pytest.mark.asyncio
    async def test_unsupported_claim_report_when_not_raising(
        self, canonical_story, make_llm_client
    ) -> None:
        """With raise_on_ungrounded=False the report carries the ungrounded verdict.

        Same out-of-source claim, but the caller chooses to inspect — the report
        must explicitly say is_grounded=False with the bad claim counted, so SP3
        can skip-and-log rather than crash.
        """
        script = _script(
            ("ALEX", "Arsenal beat Liverpool."),
            ("JORDAN", "And the manager said this was their best display in years."),
        )
        client = make_llm_client(
            _verification_response(
                ("Arsenal beat Liverpool", "SUPPORTED"),
                (
                    "the manager said this was their best display in years",
                    "UNSUPPORTED",
                ),
            )
        )

        report = await run_single_source_verification(
            script=script,
            source_story=canonical_story,
            llm_client=client,
            raise_on_ungrounded=False,
        )
        assert report.is_grounded is False
        assert report.ungrounded_claim_count == 1
        assert any(c.status == "UNSUPPORTED" for c in report.claims)

    @pytest.mark.asyncio
    async def test_contradicted_claim_is_ungrounded(
        self, canonical_story, make_llm_client
    ) -> None:
        """A CONTRADICTED claim (wrong score) is also ungrounded → halts."""
        script = _script(
            ("ALEX", "Arsenal won three to nil, right?"),
            ("JORDAN", "Yes, a comfortable three nil."),
        )
        client = make_llm_client(
            _verification_response(("Arsenal won three to nil", "CONTRADICTED"))
        )
        with pytest.raises(VerificationHaltError) as exc_info:
            await run_single_source_verification(
                script=script, source_story=canonical_story, llm_client=client
            )
        assert exc_info.value.contradicted_count == 1


class TestGroundedHappyPath:
    """A fully-supported script passes the guardrail."""

    @pytest.mark.asyncio
    async def test_all_supported_is_grounded(
        self, canonical_story, make_llm_client
    ) -> None:
        """Every claim SUPPORTED → is_grounded True, no halt, all claims returned."""
        script = _script(
            ("ALEX", "Arsenal beat Liverpool two to one."),
            (
                "JORDAN",
                "Saka scored both goals, and Arsenal went top on seventy-eight points.",
            ),
        )
        client = make_llm_client(
            _verification_response(
                ("Arsenal beat Liverpool two to one", "SUPPORTED"),
                ("Saka scored both goals", "SUPPORTED"),
                ("Arsenal top on seventy-eight points", "SUPPORTED"),
            )
        )

        report = await run_single_source_verification(
            script=script, source_story=canonical_story, llm_client=client
        )
        assert report.is_grounded is True
        assert report.ungrounded_claim_count == 0
        assert len(report.claims) == 3


class TestVerificationFailureAndEdge:
    """Failure + edge handling — the guardrail must fail safe."""

    @pytest.mark.asyncio
    async def test_missing_source_body_raises(
        self, canonical_story, make_llm_client
    ) -> None:
        """Failure: no source body means nothing to ground against → loud error."""
        bodyless = canonical_story.model_copy(update={"canonical_body_text": ""})
        script = _script(("ALEX", "hi"), ("JORDAN", "hello"))
        client = make_llm_client(_verification_response(("hi", "SUPPORTED")))
        with pytest.raises(PipelineStageError):
            await run_single_source_verification(
                script=script, source_story=bodyless, llm_client=client
            )

    @pytest.mark.asyncio
    async def test_non_object_response_raises(
        self, canonical_story, make_llm_client
    ) -> None:
        """Failure: a verifier that returns an array (not an object) is rejected."""
        script = _script(("ALEX", "hi"), ("JORDAN", "hello"))
        client = make_llm_client('["not", "an", "object"]')
        with pytest.raises(PipelineStageError):
            await run_single_source_verification(
                script=script, source_story=canonical_story, llm_client=client
            )

    def test_unknown_status_coerced_to_unsupported(self) -> None:
        """Edge/fail-safe: a garbled status must NOT read as SUPPORTED.

        The guardrail fails safe — an unparseable verdict is treated as
        UNSUPPORTED so a malformed model response can never wave a hallucination
        through.
        """
        parsed = {"claims": [{"claim": "something", "status": "MAYBE_TRUE"}]}
        claims = _parse_verification_response(parsed)
        assert len(claims) == 1
        assert claims[0].status == "UNSUPPORTED"
