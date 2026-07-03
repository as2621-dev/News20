"""Drive the REAL interview turn endpoint for the 3 M4 personas (slice #10).

Completes the conversational onboarding interview for each persona against the
production ``POST /api/interview/turn`` contract (real Gemini turns, real Supabase
JWT auth) and measures the M4 watch-items: taps, turns, wall time, and LLM
token cost per completed onboarding. Terminal micro-interests are compared by
canonical slug against the persona's seeded profile (``scripts/seed_personas.py``
— the interview persistence contract's stand-in) to prove convergence; the driver
NEVER writes profile rows itself, so the frozen 9-interest census denominator of
slice #5 stays intact.

The tap policy is a deterministic persona simulation (Rule 5 — code, not an LLM,
plays the user): tap the persona's roots on turn 1, then per turn tap up to 2
option bubbles whose label matches an uncovered target niche's keywords; when no
bubble matches an uncovered niche, use the always-present type-your-own affordance
with the persona's own phrase; when everything is covered, skip until the engine
terminates.

Usage:
    .venv/bin/python scripts/e2e/interview_persona_driver.py \
        --worker-url http://127.0.0.1:8787 --json-out /tmp/interview_runs.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.interview.constants import (  # noqa: E402
    ROOT_LABEL_BY_SLUG,
    SKIP_BUBBLE_LABEL,
    TYPE_YOUR_OWN_BUBBLE_LABEL,
)
from agents.shared.logger import get_logger  # noqa: E402
from scripts.seed_personas import PERSONA_SPECS, Persona  # noqa: E402

logger = get_logger("scripts.e2e.interview_persona_driver")

# Safety cap on turns — the engine's own TAP_BUDGET_HARD steers to terminal well
# before this; the cap only prevents a runaway loop on a misbehaving endpoint.
_MAX_TURNS = 12
# Max option bubbles tapped per turn (keeps the simulated user inside the ~15-tap
# budget the engine designs for).
_MAX_OPTION_TAPS_PER_TURN = 2
_RETRY_LIMIT_PER_TURN = 2


class InterviewRunResult(BaseModel):
    """Watch-item measurements for one persona's completed interview.

    Attributes:
        persona_key: The persona's stable key (``founder``/``cricket``/``chip``).
        completed: True when the engine returned a terminal response.
        roots_only_fallback: Terminal flag — True means the engine parked the user
            on a roots-only profile (no micro-interests extracted).
        turns: Total question turns served (including the deterministic roots turn).
        taps: Total user taps (bubble taps + 1 per free-text entry + 1 confirm).
        free_text_entries: How many type-your-own entries the simulation needed.
        retries: How many typed ``retry`` bodies the endpoint returned.
        wall_seconds: End-to-end wall time for the whole interview.
        llm_prompt_tokens: Sum of Gemini prompt tokens across turns.
        llm_output_tokens: Sum of Gemini output tokens across turns.
        llm_total_tokens: Sum of Gemini total tokens across turns.
        llm_model_names: Distinct model names reported by the turn costs.
        terminal_slugs: Canonical slugs of the terminal micro-interest list.
        seeded_slugs: Canonical slugs of the persona's seeded profile.
        converged_slugs: Terminal slugs that match the seeded profile exactly.
        divergent_slugs: Terminal slugs NOT in the seeded profile (recorded, never
            persisted by this driver).
    """

    persona_key: str
    completed: bool = False
    roots_only_fallback: bool = False
    turns: int = 0
    taps: int = 0
    free_text_entries: int = 0
    retries: int = 0
    wall_seconds: float = 0.0
    llm_prompt_tokens: int = 0
    llm_output_tokens: int = 0
    llm_total_tokens: int = 0
    llm_model_names: list[str] = Field(default_factory=list)
    terminal_slugs: list[str] = Field(default_factory=list)
    seeded_slugs: list[str] = Field(default_factory=list)
    converged_slugs: list[str] = Field(default_factory=list)
    divergent_slugs: list[str] = Field(default_factory=list)


def _strong_keywords_for_niche(niche: Any) -> set[str]:
    """Lowercased STRONG match keywords: the display label + anchor terms only.

    Slug segments are deliberately excluded — matching a broad drill bubble (e.g.
    "AI startups") on a segment word would mark the niche covered before the
    persona actually expressed it, and the engine then terminates too early.
    """
    words = {niche.display_label.lower()}
    for term in niche.search_anchor_terms:
        words.add(term.lower())
    return {w for w in words if len(w) >= 3}


def _bubble_matches(label: str, keywords: set[str]) -> bool:
    """True when a bubble label names the niche (strong containment match)."""
    lowered = label.lower()
    return any(word in lowered or lowered in word for word in keywords)


def _mint_persona_jwt(email: str) -> str:
    """Mint a real Supabase session JWT for a persona via an admin magiclink.

    Uses ``generate_link`` (admin) → ``verify_otp`` (token_hash) so the returned
    access token is a genuine user session — the worker's ``verify_supabase_user``
    accepts it exactly as it would a phone login. A THROWAWAY client is created
    per call: ``verify_otp`` replaces the client's own session with the persona's,
    which would break subsequent admin calls on a shared client (403).
    """
    from supabase import create_client

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
    link = supabase.auth.admin.generate_link({"type": "magiclink", "email": email})
    token_hash = link.properties.hashed_token
    session = supabase.auth.verify_otp({"token_hash": token_hash, "type": "magiclink"})
    return str(session.session.access_token)


def run_persona_interview(
    persona: Persona, worker_url: str, jwt: str
) -> InterviewRunResult:
    """Complete one persona's interview against the real turn endpoint."""
    result = InterviewRunResult(
        persona_key=persona.persona_key,
        seeded_slugs=sorted(n.canonical_slug for n in persona.micro_interests),
    )
    target_roots = {n.canonical_slug.split(".")[0] for n in persona.micro_interests}
    root_labels = {ROOT_LABEL_BY_SLUG[slug].casefold() for slug in target_roots}
    # Niches not yet expressed via a tap or typed entry.
    uncovered = list(persona.micro_interests)

    conversation_state: list[dict[str, Any]] = []
    started = time.monotonic()

    with httpx.Client(timeout=90.0) as client:
        for _ in range(_MAX_TURNS):
            body: dict[str, Any] | None = None
            for _attempt in range(_RETRY_LIMIT_PER_TURN + 1):
                response = client.post(
                    f"{worker_url}/api/interview/turn",
                    json={"conversation_state": conversation_state},
                    headers={"Authorization": f"Bearer {jwt}"},
                )
                response.raise_for_status()
                body = response.json()
                if body.get("response_kind") != "retry":
                    break
                result.retries += 1
            if body is None or body.get("response_kind") == "retry":
                logger.error(
                    "interview_turn_stuck_on_retry",
                    persona_key=persona.persona_key,
                    fix_suggestion="Check worker logs / Gemini quota; re-run driver.",
                )
                break

            cost = body.get("turn_cost") or {}
            result.llm_prompt_tokens += int(cost.get("prompt_tokens") or 0)
            result.llm_output_tokens += int(cost.get("output_tokens") or 0)
            result.llm_total_tokens += int(cost.get("total_tokens") or 0)
            model_name = str(cost.get("model_name") or "")
            if model_name and model_name not in result.llm_model_names:
                result.llm_model_names.append(model_name)

            if body["response_kind"] == "terminal":
                result.completed = True
                result.roots_only_fallback = bool(body.get("roots_only_fallback"))
                result.terminal_slugs = sorted(
                    str(item.get("canonical_slug", ""))
                    for item in body.get("micro_interests") or []
                )
                # The confirm tap the real UI requires at terminal.
                result.taps += 1
                break

            result.turns += 1
            bubbles = body.get("bubbles") or []
            option_labels = [
                b["bubble_label"] for b in bubbles if b.get("bubble_kind") == "option"
            ]

            tapped: list[str] = []
            free_text: str | None = None
            if result.turns == 1:
                # Roots turn: tap every root the persona's niches anchor to.
                tapped = [
                    label for label in option_labels if label.casefold() in root_labels
                ]
            else:
                for label in option_labels:
                    if len(tapped) >= _MAX_OPTION_TAPS_PER_TURN:
                        break
                    matched = next(
                        (
                            niche
                            for niche in uncovered
                            if _bubble_matches(label, _strong_keywords_for_niche(niche))
                        ),
                        None,
                    )
                    if matched is not None:
                        tapped.append(label)
                        uncovered.remove(matched)
                if not tapped and uncovered:
                    # No bubble NAMES a remaining niche — type it, the way the
                    # persona would ("Something else — type it" affordance).
                    niche = uncovered.pop(0)
                    free_text = (
                        f"{niche.display_label} — "
                        f"{', '.join(niche.search_anchor_terms[:2])}"
                    )
                    result.free_text_entries += 1
                elif not tapped:
                    tapped = [SKIP_BUBBLE_LABEL]

            result.taps += len(tapped) + (1 if free_text else 0)
            if free_text:
                # Typing rides the type-your-own affordance in the real UI.
                tapped = [TYPE_YOUR_OWN_BUBBLE_LABEL]
            conversation_state.append(
                {
                    "question_text": body.get("question_text") or "",
                    "bubbles_offered": [b["bubble_label"] for b in bubbles],
                    "bubbles_tapped": tapped,
                    "free_text_entered": free_text,
                }
            )

    result.wall_seconds = round(time.monotonic() - started, 1)
    seeded = set(result.seeded_slugs)
    result.converged_slugs = sorted(s for s in result.terminal_slugs if s in seeded)
    result.divergent_slugs = sorted(s for s in result.terminal_slugs if s not in seeded)
    return result


def main() -> int:
    """Run the interview for all 3 personas and print the watch-item table."""
    parser = argparse.ArgumentParser(description="Drive the 3 persona interviews.")
    parser.add_argument("--worker-url", default="http://127.0.0.1:8787")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))

    results: list[InterviewRunResult] = []
    for persona in PERSONA_SPECS:
        print(f"\n=== interview: {persona.persona_key} ===")
        jwt = _mint_persona_jwt(persona.persona_email)
        run = run_persona_interview(persona, args.worker_url, jwt)
        results.append(run)
        print(json.dumps(run.model_dump(), indent=2))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump([r.model_dump() for r in results], handle, indent=2)
        print(f"\nwrote {args.json_out}")

    return 0 if all(r.completed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
