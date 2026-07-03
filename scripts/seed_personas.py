"""Seed the 3 test personas' micro-interest profiles (slice #3).

The niche-ingestion slice must be demoable BEFORE the conversational interview
(slice #4) ships, so this script inserts the terminal micro-interests of three
fixed personas — a **founder**, a **cricket obsessive**, and a **chip-industry
nerd** — directly, following the EXACT slug + anchor-term conventions the interview
will emit (``reference/interview-onboarding-spec.md`` §4). Seeded nodes and
interview-minted nodes therefore converge on the same taxonomy: both mint through
the ``mint_interest_ladder`` RPC (upsert by slug), so a persona's
``sport.cricket.ipl`` is the SAME node a user who later types "IPL" lands on.

Mirrors the browser persister (``src/lib/interviewProfile.ts``): validate each
micro-interest as a backstop (root-anchored slug, ≤ 4 segments, well-formed
segments, ≥ 2 anchor terms) → mint the full root→leaf chain via the RPC → upsert a
deep, depth-weighted, strict-flagged ``user_interest_profile`` row carrying the
per-user display label. A rejected item is surfaced loudly, never silently minted.

Idempotent + convergent: the mint RPC upserts by slug (re-minting returns the same
node), the profile upsert is on the unique ``(profile_user_id, profile_interest_id)``
pair, and ``_get_or_create_user`` finds an existing auth user. Re-running converges
on the same nodes + profile rows with no duplicates.

Usage:
    .venv/bin/python scripts/seed_personas.py            # seed (writes to prod)
    .venv/bin/python scripts/seed_personas.py --dry-run  # validate + print, no writes
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any

from pydantic import BaseModel, Field

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.pipeline.categories import TOPIC_CATEGORIES  # noqa: E402
from agents.shared.logger import get_logger  # noqa: E402

logger = get_logger("scripts.seed_personas")

# The 8 canonical topic ROOT slugs a micro-interest must anchor to — imported from
# the single source of truth so this never drifts from the taxonomy (Rule 7). The
# youtube/x SOURCE axes are deliberately excluded (they are not interest roots).
TOPIC_ROOT_SLUGS: frozenset[str] = frozenset(TOPIC_CATEGORIES)

# Max drill-down depth below a root (spec §3) ⇒ at most this many dotted segments
# INCLUDING the root. Mirrors interviewProfile.MAX_SLUG_SEGMENTS.
_MAX_SLUG_SEGMENTS = 4

# Minimum distinct anchor terms a niche must carry to be searchable (spec §4).
_MIN_ANCHOR_TERMS = 2

# Default profile_weight by selection depth — the Python twin of
# ``PROFILE_WEIGHT_BY_DEPTH`` in src/lib/onboardingProfile.ts (deeper picks are more
# specific ⇒ start heavier). Kept in sync so a seeded profile row scores identically
# to a picker/interview-minted one.
_PROFILE_WEIGHT_BY_DEPTH: dict[int, float] = {0: 1.0, 1: 1.5, 2: 2.0, 3: 2.5}
_DEFAULT_PROFILE_WEIGHT = 1.0

# The interview writes ``profile_source='typed'`` for chip/typed picks; the persona
# seed is the interview's stand-in, so it uses the same source value for convergence.
_PROFILE_SOURCE = "typed"

# A slug segment: lowercase alphanumerics, dash-separated. Mirrors the RPC's
# per-segment guard and interviewProfile.SLUG_SEGMENT_RE.
_SLUG_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PersonaMicroInterest(BaseModel):
    """One terminal micro-interest for a persona (the interview §4 schema shape).

    Attributes:
        canonical_slug: Root-anchored dotted slug (e.g. ``sport.cricket.ipl``).
        display_label: The persona's own vocabulary (drives feed section headers).
        search_anchor_terms: ≥ 2 concrete anchor terms for the BigQuery batched
            query (joined into ``interest_search_query``).
        strict: When True the interest never climbs its fallback ladder
            (``profile_is_strict``).
    """

    canonical_slug: str = Field(..., description="Root-anchored dotted slug")
    display_label: str = Field(..., description="Persona's own vocabulary label")
    search_anchor_terms: list[str] = Field(
        ..., description="≥2 concrete anchor terms for the BigQuery query"
    )
    strict: bool = Field(default=False, description="No fallback climb when True")


class Persona(BaseModel):
    """A test persona: an auth user (by email) + their terminal micro-interests.

    Attributes:
        persona_key: Stable short key for logs (``founder`` / ``cricket`` / ``chip``).
        persona_email: The auth user's email (created idempotently, email-confirmed).
        micro_interests: The persona's terminal micro-interest list.
    """

    persona_key: str = Field(..., description="Stable short key for logs")
    persona_email: str = Field(..., description="Auth user email (idempotent)")
    micro_interests: list[PersonaMicroInterest] = Field(
        default_factory=list, description="Terminal micro-interests to seed"
    )


# ── The 3 fixed personas (issue #3) ───────────────────────────────────────────
# Slugs anchor to the 8 topic roots; every niche carries ≥ 2 concrete anchor terms
# (concrete entities, not stopwords — the BigQuery matcher drops filler words), so a
# real BigQuery pull returns rows for each. The "cricket obsessive" marks its core
# IPL interest strict (never climb to generic cricket).
PERSONA_SPECS: tuple[Persona, ...] = (
    Persona(
        persona_key="founder",
        persona_email="persona.founder@news20.seed",
        micro_interests=[
            PersonaMicroInterest(
                canonical_slug="ai.foundation-models",
                display_label="Foundation models",
                search_anchor_terms=[
                    "foundation models",
                    "large language models",
                    "frontier AI labs",
                ],
            ),
            PersonaMicroInterest(
                canonical_slug="business.venture-capital",
                display_label="Venture capital",
                search_anchor_terms=[
                    "venture capital",
                    "startup funding round",
                    "seed investment",
                ],
            ),
            PersonaMicroInterest(
                canonical_slug="tech.developer-tools",
                display_label="Dev tools",
                search_anchor_terms=[
                    "developer tools",
                    "software infrastructure",
                    "programming platform",
                ],
            ),
        ],
    ),
    Persona(
        persona_key="cricket",
        persona_email="persona.cricket@news20.seed",
        micro_interests=[
            PersonaMicroInterest(
                canonical_slug="sport.cricket.ipl",
                display_label="IPL",
                search_anchor_terms=[
                    "Indian Premier League",
                    "IPL cricket",
                ],
                strict=True,
            ),
            PersonaMicroInterest(
                canonical_slug="sport.cricket.india-team",
                display_label="Team India cricket",
                search_anchor_terms=[
                    "India cricket team",
                    "BCCI selection",
                ],
            ),
            PersonaMicroInterest(
                canonical_slug="sport.cricket.world-cup",
                display_label="Cricket World Cup",
                search_anchor_terms=[
                    "Cricket World Cup",
                    "ICC tournament",
                ],
            ),
        ],
    ),
    Persona(
        persona_key="chip",
        persona_email="persona.chip@news20.seed",
        micro_interests=[
            PersonaMicroInterest(
                canonical_slug="tech.semiconductors.tsmc",
                display_label="TSMC",
                search_anchor_terms=[
                    "TSMC",
                    "chip foundry",
                    "semiconductor fabrication",
                ],
            ),
            PersonaMicroInterest(
                canonical_slug="tech.semiconductors.nvidia",
                display_label="Nvidia chips",
                search_anchor_terms=[
                    "Nvidia",
                    "GPU accelerator",
                    "AI chips",
                ],
            ),
            PersonaMicroInterest(
                canonical_slug="geopolitics.chip-export-controls",
                display_label="Chip export controls",
                search_anchor_terms=[
                    "chip export controls",
                    "semiconductor sanctions",
                ],
            ),
        ],
    ),
)


def distinct_non_empty(terms: list[str]) -> list[str]:
    """Strip, drop empties, dedup case-insensitively (order preserved).

    Mirrors ``interviewProfile.distinctNonEmpty`` so a seeded query is tokenized
    identically to an interview-minted one.

    Example:
        >>> distinct_non_empty([" TSMC ", "tsmc", "", "GPU"])
        ['TSMC', 'GPU']
    """
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = term.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def build_search_query(anchor_terms: list[str]) -> str:
    """Join the distinct anchor terms into an ``interest_search_query`` string.

    The comma-joined form matches ``interviewProfile.persistInterviewInterests``
    (``distinctNonEmpty(...).join(", ")``), so the BigQuery adapter's tokenizer sees
    identical input whether the node was seeded or interview-minted.

    Example:
        >>> build_search_query(["TSMC", "chip foundry"])
        'TSMC, chip foundry'
    """
    return ", ".join(distinct_non_empty(anchor_terms))


def micro_interest_rejection_reason(item: PersonaMicroInterest) -> str | None:
    """Return why a micro-interest must be rejected, or None when it is valid.

    Backstop mirroring ``interviewProfile.rejectionReason`` — a privileged mint must
    never trust its input, so a malformed spec is dropped loudly rather than minting
    an off-shape node the downstream allocator (#6) / assembly (#7) then chokes on.

    Example:
        >>> micro_interest_rejection_reason(
        ...     PersonaMicroInterest(canonical_slug="notaroot.x",
        ...         display_label="X", search_anchor_terms=["a", "b"]))
        'slug_not_root_anchored'
    """
    slug = item.canonical_slug.strip().lower()
    if slug == "":
        return "empty_slug"
    segments = slug.split(".")
    if segments[0] not in TOPIC_ROOT_SLUGS:
        return "slug_not_root_anchored"
    if len(segments) > _MAX_SLUG_SEGMENTS:
        return "slug_too_deep"
    if not all(_SLUG_SEGMENT_RE.match(segment) for segment in segments):
        return "malformed_slug_segment"
    if item.display_label.strip() == "":
        return "empty_display_label"
    if len(distinct_non_empty(item.search_anchor_terms)) < _MIN_ANCHOR_TERMS:
        return "under_two_anchor_terms"
    return None


def resolve_profile_weight(depth_level: int) -> float:
    """Default ``profile_weight`` for a selection depth (twin of the TS map)."""
    return _PROFILE_WEIGHT_BY_DEPTH.get(depth_level, _DEFAULT_PROFILE_WEIGHT)


def _get_or_create_user(supabase: Any, email: str) -> str:
    """Get an existing auth user's id by email, or create it (idempotent seed).

    Mirrors ``scripts.run_live_batch._get_or_create_user`` so persona users are
    minted the same way the live batch mints its scoped test user.
    """
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


def seed_persona(supabase: Any, persona: Persona) -> dict[str, Any]:
    """Seed ONE persona's micro-interest profile (mint nodes → upsert profile rows).

    For each ACCEPTED micro-interest: mint its full root→leaf chain via the
    ``mint_interest_ladder`` RPC (idempotent upsert-by-slug) and stage a deep,
    depth-weighted, strict-flagged ``user_interest_profile`` row with the persona's
    display label. Nodes are minted FIRST, then the profile rows are written in ONE
    batch upsert (a mid-run failure leaves no orphaned half-profile; a retry re-runs
    cleanly). Rejected items are surfaced, never minted.

    Returns:
        A summary dict: ``persona_key``, ``user_id``, ``minted`` count, ``rejected``
        list of ``{canonical_slug, reason}``.
    """
    user_id = _get_or_create_user(supabase, persona.persona_email)
    rejected: list[dict[str, str]] = []
    # Keyed by leaf interest_id so a duplicate slug cannot produce two rows in one
    # upsert batch (Postgres forbids ON CONFLICT touching a row twice per statement).
    profile_row_by_interest_id: dict[str, dict[str, Any]] = {}

    for item in persona.micro_interests:
        reason = micro_interest_rejection_reason(item)
        if reason is not None:
            rejected.append({"canonical_slug": item.canonical_slug, "reason": reason})
            logger.warning(
                "persona_micro_interest_rejected",
                persona_key=persona.persona_key,
                canonical_slug=item.canonical_slug,
                reason=reason,
                fix_suggestion="Fix the PERSONA_SPECS entry to satisfy spec §4 "
                "(root-anchored slug, ≤4 segments, ≥2 anchor terms).",
            )
            continue

        slug = item.canonical_slug.strip().lower()
        depth_level = len(slug.split(".")) - 1
        search_query = build_search_query(item.search_anchor_terms)

        # Mint the full root→leaf ladder (idempotent) and get the leaf id. A DB error
        # here is a HARD failure (surface it — Rule 12), not an "invalid item".
        response = supabase.rpc(
            "mint_interest_ladder",
            {"p_canonical_slug": slug, "p_search_query": search_query},
        ).execute()
        leaf_interest_id = getattr(response, "data", None)
        if not leaf_interest_id:
            logger.error(
                "persona_mint_ladder_failed",
                persona_key=persona.persona_key,
                canonical_slug=slug,
                fix_suggestion="Confirm migration 0025 applied (mint_interest_ladder "
                "RPC) and the service-role key is used.",
            )
            raise RuntimeError(
                f"mint_interest_ladder returned no id for '{slug}' "
                "(fix_suggestion: confirm migration 0025 applied)."
            )
        interest_id = str(leaf_interest_id)

        profile_row_by_interest_id[interest_id] = {
            "profile_user_id": user_id,
            "profile_interest_id": interest_id,
            "profile_weight": resolve_profile_weight(depth_level),
            "profile_source": _PROFILE_SOURCE,
            "profile_is_strict": item.strict,
            "profile_display_label": item.display_label.strip(),
        }

    profile_rows = list(profile_row_by_interest_id.values())
    if profile_rows:
        supabase.table("user_interest_profile").upsert(
            profile_rows, on_conflict="profile_user_id,profile_interest_id"
        ).execute()

    logger.info(
        "persona_seeded",
        persona_key=persona.persona_key,
        user_id=user_id,
        minted=len(profile_rows),
        rejected=len(rejected),
    )
    return {
        "persona_key": persona.persona_key,
        "user_id": user_id,
        "minted": len(profile_rows),
        "rejected": rejected,
    }


def main() -> int:
    """Seed all 3 personas against Supabase (or validate-only with ``--dry-run``)."""
    parser = argparse.ArgumentParser(description="Seed the 3 test personas.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate + print the persona payloads without writing to Supabase.",
    )
    args = parser.parse_args()

    print("\n--- PERSONA SEED ---")
    total_rejected = 0
    for persona in PERSONA_SPECS:
        print(f"\n  {persona.persona_key} ({persona.persona_email})")
        for item in persona.micro_interests:
            reason = micro_interest_rejection_reason(item)
            status = "REJECT: " + reason if reason else "ok"
            if reason:
                total_rejected += 1
            print(
                f"    [{status}] {item.canonical_slug}"
                f"{'  (strict)' if item.strict else ''} "
                f"→ {build_search_query(item.search_anchor_terms)!r}"
            )

    if args.dry_run:
        print(
            f"\nDRY-RUN complete — no writes. "
            f"{total_rejected} rejected item(s) across all personas.\n"
        )
        return 1 if total_rejected else 0

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )

    print("\n--- WRITING TO SUPABASE ---")
    summaries = [seed_persona(supabase, persona) for persona in PERSONA_SPECS]
    print("\n--- SUMMARY ---")
    for summary in summaries:
        print(
            f"  {summary['persona_key']:<8} user={summary['user_id']} "
            f"minted={summary['minted']} rejected={len(summary['rejected'])}"
        )
        for reject in summary["rejected"]:
            print(f"      REJECT {reject['canonical_slug']}: {reject['reason']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
