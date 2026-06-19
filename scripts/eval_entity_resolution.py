"""LLM eval: does concept extraction resolve the REAL named person in a story?

Phase 0c sub-phase 2 (L1). Runs the *real* ``extract_story_concept`` over a small
set of fixtures and asserts the resolved ``entity_name`` is the person the story
TEXT names — never a role ("Fed chair") and never the model's own stale prior of
who holds an office (e.g. Jerome Powell for a new-Fed-chair story, Joe Biden for a
Trump-led G7).

This is an LLM-dependent eval (Rule 12 — flagged): it calls the live Gemini model
and needs ``GEMINI_API_KEY`` in ``.env``. It is guarded behind ``__main__`` so it
never runs on import. Prints PASS/FAIL per fixture and exits non-zero on any
failure.

Run:
    python scripts/eval_entity_resolution.py
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from google import genai  # noqa: E402 — must follow the sys.path bootstrap above

from agents.m0.story_concept import extract_story_concept  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402
from agents.shared.settings import Settings  # noqa: E402

logger = get_logger("scripts.eval_entity_resolution")


@dataclass(frozen=True)
class EntityResolutionFixture:
    """One eval case: a story plus an assertion on the resolved entity name.

    Attributes:
        fixture_name: Human-readable case id, printed in the PASS/FAIL line.
        headline: The story headline.
        story_body: The full story body text (the only source of truth for who).
        story_date: ISO date the resolution is anchored to.
        assertion: Predicate over the resolved ``entity_name`` — True == PASS.
        expectation: Human-readable description of what the assertion requires.
    """

    fixture_name: str
    headline: str
    story_body: str
    story_date: str
    assertion: Callable[[str], bool]
    expectation: str


def _contains(name: str, needle: str) -> bool:
    """Case-insensitive substring check."""
    return needle.lower() in name.lower()


# Reason: "Kevin Warsh" is an invented-but-plausible NEW chair so the model cannot
# lean on a memorized current incumbent — it must read the story text.
FIXTURES: tuple[EntityResolutionFixture, ...] = (
    EntityResolutionFixture(
        fixture_name="fed_chair_named_person_not_powell",
        headline="Trump names Kevin Warsh as Federal Reserve chair",
        story_body=(
            "President Donald Trump on Monday named Kevin Warsh as the next chair of the Federal "
            "Reserve, elevating the longtime monetary-policy hawk to lead the U.S. central bank. "
            "Warsh, a former Fed governor, will succeed the outgoing chair when the current term "
            "ends. The White House said Warsh's nomination reflects the administration's push for "
            "a tighter, more rules-based approach to interest rates."
        ),
        story_date="2026-02-02",
        assertion=lambda name: (
            _contains(name, "Warsh")
            and not _contains(name, "Powell")
            and not _contains(name, "Fed chair")
            and not _contains(name, "chair")
        ),
        expectation="entity_name resolves to 'Kevin Warsh' (NOT 'Jerome Powell', NOT 'Fed chair')",
    ),
    EntityResolutionFixture(
        fixture_name="g7_summit_primary_leader_trump_not_biden",
        headline="G7 leaders gather as Trump pushes new trade terms",
        story_body=(
            "The Group of Seven summit opened with U.S. President Donald Trump at the center of "
            "negotiations, pressing allies to accept new tariff terms. Trump dominated the opening "
            "session, clashing with European leaders over trade and defense spending. Other heads "
            "of government attended, but the agenda was driven almost entirely by Trump's demands."
        ),
        story_date="2026-06-15",
        assertion=lambda name: (
            _contains(name, "Trump") and not _contains(name, "Biden")
        ),
        expectation="entity_name resolves to the PRIMARY leader 'Donald Trump' (NOT 'Joe Biden')",
    ),
    EntityResolutionFixture(
        fixture_name="control_company_story",
        headline="Nvidia reports record quarterly revenue on AI demand",
        story_body=(
            "Nvidia reported record quarterly revenue, driven by surging demand for its AI data-center "
            "chips. The company said data-center sales more than doubled year over year as cloud "
            "providers raced to expand capacity. Nvidia's results underscored its dominance in the "
            "market for AI accelerators."
        ),
        story_date="2026-05-20",
        assertion=lambda name: _contains(name, "Nvidia"),
        expectation="entity_name resolves to the company 'Nvidia'",
    ),
    EntityResolutionFixture(
        fixture_name="control_single_named_person",
        headline="Jensen Huang unveils Nvidia's next-generation GPU at keynote",
        story_body=(
            "Nvidia chief executive Jensen Huang took the stage to unveil the company's "
            "next-generation GPU architecture. Huang walked through performance gains and demoed "
            "live AI workloads, drawing a standing ovation from developers in the audience."
        ),
        story_date="2026-03-18",
        assertion=lambda name: _contains(name, "Huang"),
        expectation="entity_name resolves to the named person 'Jensen Huang'",
    ),
)


def run_eval() -> int:
    """Run every fixture through the real extractor and report PASS/FAIL.

    Returns:
        Process exit code: 0 when all fixtures pass, 1 otherwise.
    """
    settings = Settings()
    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        logger.error(
            "gemini_api_key_missing",
            fix_suggestion="Set GEMINI_API_KEY in .env (project root) to run this LLM eval.",
        )
        print("SKIP: GEMINI_API_KEY missing — eval authored but not executed.")
        return 1

    client = genai.Client(api_key=api_key)
    failures = 0
    for fixture in FIXTURES:
        concept = extract_story_concept(
            headline=fixture.headline,
            summary=fixture.story_body,
            client=client,
            story_body=fixture.story_body,
            story_date=fixture.story_date,
        )
        resolved_name = concept.entity_name
        passed = fixture.assertion(resolved_name)
        status = "PASS" if passed else "FAIL"
        if not passed:
            failures += 1
        print(
            f"[{status}] {fixture.fixture_name}: resolved entity_name={resolved_name!r} "
            f"entity_key={concept.entity_key!r} entity_as_of={concept.entity_as_of!r} "
            f"| expected: {fixture.expectation}"
        )

    total = len(FIXTURES)
    print(f"\n{total - failures}/{total} fixtures passed.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run_eval())
