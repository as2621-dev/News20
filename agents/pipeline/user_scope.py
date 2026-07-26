"""Seed/test-account exclusion for the shared ingestion pool (founder directive 2026-07-26).

The daily batch enumerates its ACTIVE users straight from ``user_interest_profile``
rows — but test fixtures live in the same prod tables: the 3 M4 validation personas
(``persona.*@news20.seed``, seeded by ``scripts/seed_personas.py``) and the demo
accounts (``demo-*@news20.demo``, seeded by ``scripts.run_live_batch._seed_demo_users``).
Left unfiltered, their followed interests flow into the SHARED candidate pool and can
surface stories in a real user's feed (observed 2026-07-26: 12 of 76 shortlist rows
matched only persona semiconductor interests).

Founder directive (2026-07-26): test-account interests must NEVER shape a real
batch. ONE contract at every consumer: seed accounts are excluded unless
``INCLUDE_SEED_USERS=1``. A #10 persona validation run sets BOTH
``ONLY_USER_EMAIL=<personas>`` and ``INCLUDE_SEED_USERS=1`` — scoping alone does not
re-admit them, because the pipeline's internal user loaders (produce caps, Stage D
feeds) enumerate users independently of the entry point's scope.

Both production entry points (``scripts/run_live_batch.py`` and the worker's batch
route) consume THIS module so they cannot drift on who counts as active — the same
shared-seam doctrine as ``agents/pipeline/run_flags.py`` (issues #66/#68).
"""

from __future__ import annotations

import os
from typing import Any

from agents.shared.logger import get_logger

logger = get_logger("pipeline.user_scope")

# Reason: seed accounts are recognised by their reserved email domains — both seeders
# mint users exclusively on these, and no real signup path can produce them (auth is
# email-OTP; these domains receive no mail).
SEED_ACCOUNT_EMAIL_DOMAINS: tuple[str, ...] = ("news20.seed", "news20.demo")

INCLUDE_SEED_USERS_ENV_VAR: str = "INCLUDE_SEED_USERS"


def is_seed_account_email(account_email: str) -> bool:
    """Return True when an email belongs to a reserved seed/test-account domain.

    Args:
        account_email: The auth user's email address (any case; blank allowed).

    Returns:
        True for ``*@news20.seed`` / ``*@news20.demo`` addresses, False otherwise
        (including blank/malformed values — an unknown account is treated as real,
        never silently dropped).

    Example:
        >>> is_seed_account_email("persona.cricket@news20.seed")
        True
        >>> is_seed_account_email("ash@gmail.com")
        False
    """
    domain = (
        account_email.rsplit("@", 1)[-1].strip().lower() if "@" in account_email else ""
    )
    return domain in SEED_ACCOUNT_EMAIL_DOMAINS


def seed_exclusion_enabled() -> bool:
    """Return True unless ``INCLUDE_SEED_USERS=1`` explicitly re-admits seed accounts.

    Example:
        >>> os.environ.pop("INCLUDE_SEED_USERS", None) and None
        >>> seed_exclusion_enabled()
        True
    """
    return os.environ.get(INCLUDE_SEED_USERS_ENV_VAR, "").strip() != "1"


def seed_account_user_ids(supabase_client: Any) -> set[str]:
    """Collect the auth user ids of every seed/test account (paginated walk).

    Args:
        supabase_client: A service-role Supabase client (``auth.admin`` available).

    Returns:
        The set of user ids whose email matches a seed domain. Empty when none exist.

    Example:
        >>> ids = seed_account_user_ids(supabase)  # doctest: +SKIP
        >>> isinstance(ids, set)  # doctest: +SKIP
        True
    """
    # Reason: list_users() is paginated (default 50/page) and returns a bare list on
    # newer clients but an object with ``.users`` on older ones — same dual shape the
    # persona seeder already handles. Walk every page or a persona on page 2 leaks.
    seed_user_ids: set[str] = set()
    page_number = 1
    try:
        while True:
            page = supabase_client.auth.admin.list_users(page=page_number, per_page=200)
            users = page if isinstance(page, list) else getattr(page, "users", []) or []
            if not users:
                break
            for user in users:
                if is_seed_account_email(str(getattr(user, "email", "") or "")):
                    seed_user_ids.add(str(user.id))
            page_number += 1
    except (AttributeError, TypeError):
        # Reason: pure fixture clients (tests) expose only .table() — they hold no
        # seed accounts by construction, so an empty set is the true answer there.
        # Logged loudly so a REAL client losing auth.admin can never silently
        # re-admit seed accounts without a trace in the run log.
        logger.warning(
            "seed_account_lookup_unavailable",
            collected_before_failure=len(seed_user_ids),
            fix_suggestion=(
                "Client has no usable auth.admin.list_users; seed-account exclusion "
                "is inactive this run. Expected only for test fixture clients — a "
                "production client here means the service-role key lost admin scope."
            ),
        )
    return seed_user_ids


def exclude_seed_profile_rows(
    profile_rows: list[dict[str, Any]],
    seed_user_ids: set[str],
) -> list[dict[str, Any]]:
    """Drop profile rows owned by seed/test accounts from the batch's active set.

    Pure over its inputs. Logs the exclusion loudly (Rule 12) so a run's active-user
    arithmetic is always explainable from its log.

    Args:
        profile_rows: Raw ``user_interest_profile`` rows (``profile_user_id`` keyed).
        seed_user_ids: Ids from :func:`seed_account_user_ids`.

    Returns:
        The rows whose owner is NOT a seed account (original order preserved).

    Example:
        >>> rows = [{"profile_user_id": "real"}, {"profile_user_id": "seed"}]
        >>> exclude_seed_profile_rows(rows, {"seed"})
        [{'profile_user_id': 'real'}]
    """
    kept = [
        r for r in profile_rows if str(r.get("profile_user_id")) not in seed_user_ids
    ]
    excluded_count = len(profile_rows) - len(kept)
    if excluded_count:
        logger.info(
            "seed_account_profiles_excluded",
            excluded_profile_rows=excluded_count,
            excluded_user_count=len(
                {str(r.get("profile_user_id")) for r in profile_rows} & seed_user_ids
            ),
            kept_profile_rows=len(kept),
            fix_suggestion=(
                "Expected (founder directive 2026-07-26). To run a persona "
                "validation, set BOTH ONLY_USER_EMAIL=<personas> and "
                "INCLUDE_SEED_USERS=1."
            ),
        )
    return kept
