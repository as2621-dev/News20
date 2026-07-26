"""Tests for agents/pipeline/user_scope.py — seed/test-account exclusion.

WHY these tests exist (Rule 9): founder directive 2026-07-26 — the M4 personas and
demo accounts live in prod tables, and their interests must never shape a real
batch's shared pool (observed leak: 12/76 shortlist rows from persona interests).
These tests pin the exclusion contract: seed domains recognised, every auth page
walked, real users never dropped, and INCLUDE_SEED_USERS=1 as the only re-admit.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agents.pipeline.user_scope import (
    exclude_seed_profile_rows,
    is_seed_account_email,
    seed_account_user_ids,
    seed_exclusion_enabled,
)


class _FakeAdmin:
    """Paginated list_users double (newer-client shape: bare list per page)."""

    def __init__(self, pages: list[list[Any]]) -> None:
        self._pages = pages
        self.calls: list[int] = []

    def list_users(self, page: int = 1, per_page: int = 200) -> list[Any]:
        self.calls.append(page)
        return self._pages[page - 1] if page <= len(self._pages) else []


def _client_with_pages(pages: list[list[Any]]) -> Any:
    return SimpleNamespace(auth=SimpleNamespace(admin=_FakeAdmin(pages)))


def _user(user_id: str, email: str) -> Any:
    return SimpleNamespace(id=user_id, email=email)


# ── is_seed_account_email ─────────────────────────────────────────────────────


def test_is_seed_account_email_detects_both_seed_domains() -> None:
    assert is_seed_account_email("persona.cricket@news20.seed")
    assert is_seed_account_email("demo-ai-foundation-models@news20.demo")
    assert is_seed_account_email("PERSONA.CHIP@NEWS20.SEED")  # case-insensitive


def test_is_seed_account_email_real_users_are_not_seed() -> None:
    assert not is_seed_account_email("ash@gmail.com")
    # A real user whose LOCAL part mentions a seed domain is still real.
    assert not is_seed_account_email("news20.seed@gmail.com")


def test_is_seed_account_email_blank_or_malformed_is_not_seed() -> None:
    # Edge: unknown/malformed accounts are treated as real — never silently dropped.
    assert not is_seed_account_email("")
    assert not is_seed_account_email("no-at-sign")


# ── seed_exclusion_enabled ────────────────────────────────────────────────────


def test_seed_exclusion_enabled_by_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("INCLUDE_SEED_USERS", raising=False)
    assert seed_exclusion_enabled()


def test_include_seed_users_flag_disables_exclusion(monkeypatch: Any) -> None:
    monkeypatch.setenv("INCLUDE_SEED_USERS", "1")
    assert not seed_exclusion_enabled()
    # Failure-ish edge: any value other than exactly "1" keeps the exclusion ON.
    monkeypatch.setenv("INCLUDE_SEED_USERS", "true")
    assert seed_exclusion_enabled()


# ── seed_account_user_ids ─────────────────────────────────────────────────────


def test_seed_account_user_ids_walks_every_page() -> None:
    # Happy path: a seed account on page 2 is found (single-page scan would leak it).
    client = _client_with_pages(
        [
            [_user("u-real", "ash@gmail.com")],
            [_user("u-seed", "persona.founder@news20.seed")],
        ]
    )
    assert seed_account_user_ids(client) == {"u-seed"}
    assert client.auth.admin.calls == [1, 2, 3]  # walked until the empty page


def test_seed_account_user_ids_without_admin_returns_empty() -> None:
    # Edge: pure fixture clients expose only .table() — empty set, no crash.
    assert seed_account_user_ids(SimpleNamespace(table=lambda name: None)) == set()


def test_seed_account_user_ids_handles_users_attribute_shape() -> None:
    # Older client shape: page object carrying .users instead of a bare list.
    page_obj = SimpleNamespace(users=[_user("u-demo", "demo-x@news20.demo")])
    empty_obj = SimpleNamespace(users=[])
    pages = [page_obj, empty_obj]

    class _Admin:
        def list_users(self, page: int = 1, per_page: int = 200) -> Any:
            return pages[page - 1] if page <= len(pages) else empty_obj

    client = SimpleNamespace(auth=SimpleNamespace(admin=_Admin()))
    assert seed_account_user_ids(client) == {"u-demo"}


# ── exclude_seed_profile_rows ─────────────────────────────────────────────────


def test_exclude_seed_profile_rows_drops_only_seed_rows_in_order() -> None:
    rows = [
        {"profile_user_id": "u-real", "profile_interest_id": "i1"},
        {"profile_user_id": "u-seed", "profile_interest_id": "i2"},
        {"profile_user_id": "u-real", "profile_interest_id": "i3"},
    ]
    kept = exclude_seed_profile_rows(rows, {"u-seed"})
    assert kept == [rows[0], rows[2]]


def test_exclude_seed_profile_rows_no_seed_ids_is_a_noop() -> None:
    rows = [{"profile_user_id": "u-real"}]
    assert exclude_seed_profile_rows(rows, set()) == rows


def test_exclude_seed_profile_rows_empty_rows_stay_empty() -> None:
    assert exclude_seed_profile_rows([], {"u-seed"}) == []
