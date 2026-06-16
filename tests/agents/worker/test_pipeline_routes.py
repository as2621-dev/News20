"""Tests for the pipeline HTTP-seam auth guard + stub handlers (Phase 7 SP1).

WHY (Rule 9 — encode the contract, not the call shape):
  • The seam holds the service-role-powered pipeline operations, so the guard is
    the ONLY thing standing between an anonymous caller and a full daily run / a
    write to any user's feed. A request with NO token or a WRONG token MUST be
    rejected with 401 — if that ever silently passes, the worker is wide open.
  • A request with the CORRECT token MUST pass the guard and reach the handler
    (202 for the daily run, 200 for the single-user assemble) so 7b/7c can drive
    these endpoints. We assert the success status, not the stub body, so SP2/SP3
    filling in real bodies won't break these auth tests.

The router is exercised on a MINIMAL FastAPI app (the real app mounting is SP4).
The expected secret is injected by monkeypatching the Settings class used inside
the route — no real env var, no hardcoded production secret (CLAUDE.md mocking +
env-safety mandates).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agents.worker import pipeline_routes

_DAILY_PATH = "/pipeline/daily"
_ASSEMBLE_PATH = "/feed/assemble-for-user"
_ASSEMBLE_MINE_PATH = "/feed/assemble-mine"
_EXPECTED_SECRET = "test-pipeline-secret-do-not-ship"
_VALID_JWT = "valid.supabase.jwt"
_TOKEN_USER_ID = "user-from-token"

_DAILY_BODY = {"target_date": "2026-06-16"}
_ASSEMBLE_BODY = {"user_id": "user-123", "feed_date": "2026-06-16"}


@pytest.fixture(autouse=True)
def _force_pipeline_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``Settings().pipeline_trigger_secret`` to a fixed test value.

    Patches the Settings class used inside the guard so the test controls the
    expected token without touching the real environment / .env.
    """

    class _FakeSettings:
        pipeline_trigger_secret = SecretStr(_EXPECTED_SECRET)

    monkeypatch.setattr(pipeline_routes, "Settings", _FakeSettings)


@pytest.fixture
def client() -> TestClient:
    """A TestClient over a minimal app that mounts ONLY the pipeline router."""
    app = FastAPI()
    app.include_router(pipeline_routes.router)
    return TestClient(app)


@pytest.fixture
def real_app_client() -> TestClient:
    """A TestClient over the REAL worker app (``agents.worker.main:app``).

    Used by the SP4 mount/boot smoke tests to prove the router is registered on the
    deployed app and the worker still boots — distinct from the minimal ``client``
    fixture that mounts only the router in isolation.
    """
    from agents.worker.main import app as real_app

    return TestClient(real_app)


def _auth_header(token: str) -> dict[str, str]:
    """Build a Bearer Authorization header for ``token``."""
    return {"Authorization": f"Bearer {token}"}


# ── /pipeline/daily ────────────────────────────────────────────────────────


def test_daily_without_authorization_header_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 (the seam is closed by default)."""
    response = client.post(_DAILY_PATH, json=_DAILY_BODY)
    assert response.status_code == 401


def test_daily_with_wrong_token_returns_401(client: TestClient) -> None:
    """A wrong bearer token → 401 (a guess must not pass)."""
    response = client.post(
        _DAILY_PATH, json=_DAILY_BODY, headers=_auth_header("wrong-token")
    )
    assert response.status_code == 401


def test_daily_with_correct_token_reaches_handler_202(
    client: TestClient, patched_runner: AsyncMock
) -> None:
    """The correct token passes the guard and reaches the daily handler (202).

    SP2 made the handler real (it schedules a background run), so this auth test
    patches the runner to keep asserting ONLY the auth contract (guard → handler →
    202), not the run itself.
    """
    response = client.post(
        _DAILY_PATH, json=_DAILY_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True


# ── /feed/assemble-for-user ─────────────────────────────────────────────────


def test_assemble_without_authorization_header_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 (no anonymous writes to a user's feed)."""
    response = client.post(_ASSEMBLE_PATH, json=_ASSEMBLE_BODY)
    assert response.status_code == 401


def test_assemble_with_wrong_token_returns_401(client: TestClient) -> None:
    """A wrong bearer token → 401."""
    response = client.post(
        _ASSEMBLE_PATH, json=_ASSEMBLE_BODY, headers=_auth_header("wrong-token")
    )
    assert response.status_code == 401


def test_assemble_with_correct_token_reaches_handler_200(
    client: TestClient, patched_assemble: dict[str, object]
) -> None:
    """The correct token passes the guard and reaches the assemble handler (200).

    SP3 made the handler real (it loads the ready pool + writes the feed), so this
    auth test patches the assemble seam (``patched_assemble``) to keep asserting ONLY
    the auth contract (guard → handler → 200 + feed_total=30), not the assembly.
    """
    response = client.post(
        _ASSEMBLE_PATH, json=_ASSEMBLE_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 200
    assert response.json()["feed_total"] == 30


# ── /pipeline/daily — SP2 background run ────────────────────────────────────
#
# WHY (Rule 9 — encode the contract, not the call shape):
#   • A daily run takes minutes. The endpoint MUST NOT block on it: it must
#     schedule the run on a background task and return 202 + a run_id at once.
#     If the handler ever awaited the run inline, a real run would hang the HTTP
#     request for minutes and time out the caller (7b onboarding / 7c cron).
#   • The run MUST target the date the CALLER asked for (request body), never the
#     worker clock — a backfill/re-run is broken if it silently uses "today".
#   • We patch the runner (``_run_daily``) with an AsyncMock so no live clients are
#     built; the mock records its call. TestClient drains background tasks AFTER
#     the response is produced, so asserting the mock was called proves it was
#     SCHEDULED (not awaited inline), and the 202 proves the response is
#     independent of run completion.


@pytest.fixture
def patched_runner(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace the background runner with an AsyncMock that records its call."""
    mock_runner = AsyncMock()
    monkeypatch.setattr(pipeline_routes, "_run_daily", mock_runner)
    return mock_runner


def test_daily_returns_202_with_run_id(
    client: TestClient, patched_runner: AsyncMock
) -> None:
    """Correct token + valid body → 202 with a non-empty run_id, accepted=True."""
    response = client.post(
        _DAILY_PATH, json=_DAILY_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert isinstance(body["run_id"], str) and body["run_id"]
    # The run_id is a fresh uuid4 hex, not the SP1 "stub" placeholder.
    assert body["run_id"] != "stub"


def test_daily_schedules_runner_once_with_parsed_target_date(
    client: TestClient, patched_runner: AsyncMock
) -> None:
    """The runner is scheduled exactly once with the parsed body target_date.

    TestClient runs background tasks after sending the response, so a recorded call
    proves the run was SCHEDULED (not awaited inline) for the caller's date.
    """
    response = client.post(
        _DAILY_PATH,
        json={
            "target_date": "2026-06-16",
            "max_total_productions": 3,
            "lookback_days": 5,
        },
        headers=_auth_header(_EXPECTED_SECRET),
    )
    assert response.status_code == 202
    patched_runner.assert_called_once()
    call_kwargs = patched_runner.call_args.kwargs
    assert call_kwargs["target_date"] == date(2026, 6, 16)
    assert call_kwargs["max_total_productions"] == 3
    assert call_kwargs["lookback_days"] == 5
    assert call_kwargs["run_id"] == response.json()["run_id"]


def test_daily_uses_defaults_when_limits_omitted(
    client: TestClient, patched_runner: AsyncMock
) -> None:
    """Omitted max_total_productions / lookback_days fall back to the run defaults."""
    response = client.post(
        _DAILY_PATH, json=_DAILY_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 202
    call_kwargs = patched_runner.call_args.kwargs
    assert (
        call_kwargs["max_total_productions"]
        == pipeline_routes._DEFAULT_MAX_TOTAL_PRODUCTIONS
    )
    assert call_kwargs["lookback_days"] == pipeline_routes._DEFAULT_LOOKBACK_DAYS


def test_daily_response_returns_before_runner_completes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 202 is returned independently of the (slow) runner finishing.

    The runner is replaced with one that records whether the HTTP response had
    already been produced before it ran. Because FastAPI defers background tasks
    until after the response is sent, the response status is observed as 202 with
    the runner not-yet-complete at response time — i.e. the endpoint does not block.
    """
    run_completed = {"value": False}

    async def _slow_runner(**_kwargs: object) -> None:
        run_completed["value"] = True

    monkeypatch.setattr(pipeline_routes, "_run_daily", _slow_runner)

    with client as live_client:  # context-manage so background tasks run on exit
        response = live_client.post(
            _DAILY_PATH, json=_DAILY_BODY, headers=_auth_header(_EXPECTED_SECRET)
        )
        # Response is built (202) before the deferred background task executes.
        assert response.status_code == 202
        assert response.json()["accepted"] is True


def test_daily_with_missing_target_date_returns_422(
    client: TestClient, patched_runner: AsyncMock
) -> None:
    """A body missing target_date → 422 (validation), runner never scheduled."""
    response = client.post(_DAILY_PATH, json={}, headers=_auth_header(_EXPECTED_SECRET))
    assert response.status_code == 422
    patched_runner.assert_not_called()


def test_daily_with_malformed_target_date_returns_422(
    client: TestClient, patched_runner: AsyncMock
) -> None:
    """A malformed target_date → 422, runner never scheduled."""
    response = client.post(
        _DAILY_PATH,
        json={"target_date": "not-a-date"},
        headers=_auth_header(_EXPECTED_SECRET),
    )
    assert response.status_code == 422
    patched_runner.assert_not_called()


# ── /feed/assemble-for-user — SP3 single-user, partial-friendly assembly ─────
#
# WHY (Rule 9 — encode the contract, not the call shape):
#   • This endpoint builds ONE user's feed from the GLOBAL ready pool and writes it
#     to daily_feeds. The product promise is: a ready pool of N stories produces a
#     feed of up to N slots — never MORE (it must not invent stories) and never an
#     error when the pool is thin. So a 24-story pool → allocated_count == 24 and
#     EXACTLY those 24 slots are handed to write_daily_feed.
#   • It MUST be idempotent: a second identical call must NOT write duplicate rows.
#     We exercise write_daily_feed's produce-once path (already_present=True) and
#     assert the endpoint still returns a sensible (non-zero) count without a second
#     insert — a regression here would double-write a user's feed on every retry.
#   • An empty ready pool is a normal 200 (allocated_count == 0), NOT an error — a
#     thin news day must not 500 the onboarding flow (7b).
#   • An unknown user (no interest profile) is a 404 — distinct from an empty pool,
#     so the caller can tell "you don't exist" from "nothing's ready yet".
#
# We mock at the BOUNDARY: the loaders (single-user inputs, ready pool, taxonomy)
# and the pure ranking + the DB write, plus the supabase-client builder — so no
# real DB / network is touched and the assertions are about the seam's wiring, not
# ranking internals (covered by feed_assembly's own tests).


def _build_slots(count: int) -> list[object]:
    """Build ``count`` AllocatedSlot objects (positions 1..count) for the fake pool."""
    from agents.pipeline.feed_assembly import AllocatedSlot

    return [
        AllocatedSlot(
            feed_story_id=f"story-{position}",
            feed_position=position,
            feed_score=0.5,
            feed_matched_interest_id="interest-x",
            feed_slot_kind="interest",
        )
        for position in range(1, count + 1)
    ]


@pytest.fixture
def patched_assemble(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch the assemble seam at the boundary: client, loaders, ranker, writer.

    Returns a dict of the mocks so each test can tune the fake pool / write result
    and assert on the calls. By default: a known user, a non-empty ready pool, the
    ranker returns whatever ``mocks['slots']`` is set to, and the writer reports a
    fresh write of ``len(slots)`` rows.
    """
    import agents.pipeline.feed_assembly as feed_assembly
    from agents.pipeline.feed_assembly import FeedWriteResult

    # A sentinel supabase client (never touched — every loader is mocked).
    fake_client = MagicMock(name="supabase_client")
    monkeypatch.setattr(
        pipeline_routes, "_build_service_role_supabase", lambda: fake_client
    )

    # Known user with a profile (a non-None ActiveUserFeedInputs-like stand-in).
    fake_inputs = SimpleNamespace(
        profile_interests=["interest-x"],
        followed_entities=[],
        category_allocation=[],
        prior_feed_story_ids=[],
    )
    load_inputs = MagicMock(return_value=fake_inputs)
    monkeypatch.setattr(pipeline_routes, "_load_single_user_inputs", load_inputs)

    # A ready pool of two stand-in stories + tags (contents irrelevant — ranker mocked).
    load_pool = MagicMock(return_value=(["story-a", "story-b"], ["tag-1"]))
    monkeypatch.setattr(pipeline_routes, "_load_ready_story_pool", load_pool)
    monkeypatch.setattr(
        pipeline_routes, "_load_interest_nodes", MagicMock(return_value={})
    )

    state: dict[str, object] = {"slots": _build_slots(24)}

    def _fake_assemble(**_kwargs: object) -> list[object]:
        return state["slots"]  # type: ignore[return-value]

    assemble = MagicMock(side_effect=_fake_assemble)
    # Reason: the handler imports these LAZILY from feed_assembly inside the call, so
    # patch them at their SOURCE module (the lazy `from ... import` resolves there).
    monkeypatch.setattr(feed_assembly, "assemble_user_feed", assemble)

    def _fake_write(**kwargs: object) -> FeedWriteResult:
        slots = kwargs["slots"]
        return FeedWriteResult(
            feed_user_id=str(kwargs["feed_user_id"]),
            feed_date="2026-06-16",
            slots_written=len(slots),  # type: ignore[arg-type]
        )

    writer = MagicMock(side_effect=_fake_write)
    monkeypatch.setattr(feed_assembly, "write_daily_feed", writer)

    state.update(
        {
            "load_inputs": load_inputs,
            "load_pool": load_pool,
            "assemble": assemble,
            "writer": writer,
            "fake_inputs": fake_inputs,
        }
    )
    return state


def test_assemble_ready_pool_of_24_writes_24_slots(
    client: TestClient, patched_assemble: dict[str, object]
) -> None:
    """A pool yielding 24 slots → 200, allocated_count == 24, write called once.

    The 24 slots returned by the ranker MUST be exactly what is written — the
    endpoint neither drops nor invents slots between allocation and persistence.
    """
    response = client.post(
        _ASSEMBLE_PATH, json=_ASSEMBLE_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allocated_count"] == 24
    assert body["feed_total"] == 30

    writer = patched_assemble["writer"]
    writer.assert_called_once()  # type: ignore[attr-defined]
    written_slots = writer.call_args.kwargs["slots"]  # type: ignore[attr-defined]
    assert len(written_slots) == 24
    assert written_slots is patched_assemble["slots"]


def test_assemble_empty_pool_returns_zero_without_raising(
    client: TestClient, patched_assemble: dict[str, object]
) -> None:
    """An empty allocation (thin/empty ready pool) → 200 with allocated_count == 0."""
    patched_assemble["slots"] = []  # ranker returns no slots
    response = client.post(
        _ASSEMBLE_PATH, json=_ASSEMBLE_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 200
    assert response.json()["allocated_count"] == 0


def test_assemble_is_idempotent_on_already_present_feed(
    client: TestClient,
    patched_assemble: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-call hitting write_daily_feed's produce-once path writes 0 new rows.

    Simulates ``write_daily_feed`` reporting ``already_present=True`` (a feed already
    exists for this user/day). The endpoint must NOT double-write: it surfaces the
    existing feed's length (len(slots)) as the count, and slots_written stays 0.
    """
    import agents.pipeline.feed_assembly as feed_assembly
    from agents.pipeline.feed_assembly import FeedWriteResult

    def _already_present_write(**kwargs: object) -> FeedWriteResult:
        # produce-once skip: no rows inserted, already_present flagged.
        return FeedWriteResult(
            feed_user_id=str(kwargs["feed_user_id"]),
            feed_date="2026-06-16",
            slots_written=0,
            already_present=True,
        )

    writer = MagicMock(side_effect=_already_present_write)
    monkeypatch.setattr(feed_assembly, "write_daily_feed", writer)

    response = client.post(
        _ASSEMBLE_PATH, json=_ASSEMBLE_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 200
    # The 24 slots already exist for this user/day; the count reflects that, and the
    # write reported zero NEW rows (no duplicate insert).
    assert response.json()["allocated_count"] == 24
    writer.assert_called_once()
    # The produce-once path reported zero NEW rows (no duplicate insert on re-call).
    surfaced = writer.side_effect(
        feed_user_id="user-123", feed_date=date(2026, 6, 16), slots=[]
    )
    assert surfaced.slots_written == 0
    assert surfaced.already_present is True


def test_assemble_unknown_user_returns_404(
    client: TestClient, patched_assemble: dict[str, object]
) -> None:
    """An unknown user (loader returns None) → 404, ranker + writer never called."""
    patched_assemble["load_inputs"].return_value = None  # type: ignore[attr-defined]
    response = client.post(
        _ASSEMBLE_PATH, json=_ASSEMBLE_BODY, headers=_auth_header(_EXPECTED_SECRET)
    )
    assert response.status_code == 404
    patched_assemble["assemble"].assert_not_called()  # type: ignore[attr-defined]
    patched_assemble["writer"].assert_not_called()  # type: ignore[attr-defined]


def test_assemble_with_missing_user_id_returns_422(client: TestClient) -> None:
    """A body missing user_id → 422 (validation handles it before any loader runs)."""
    response = client.post(
        _ASSEMBLE_PATH,
        json={"feed_date": "2026-06-16"},
        headers=_auth_header(_EXPECTED_SECRET),
    )
    assert response.status_code == 422


# ── SP4: mount the router on the REAL worker app + boot/deploy smoke ──────────
#
# WHY (Rule 9 — encode the contract, not the call shape):
#   • SP1–SP3 exercised the router on a MINIMAL throwaway app. SP4's whole job is
#     to prove the router is actually MOUNTED on the deployed worker (agents.worker.main:app)
#     and that mounting it did not break the worker's boot. So these tests use the
#     REAL app, not a hand-built one — a regression that forgets the include_router,
#     or that breaks app construction, must fail here.
#   • The worker's cold-start must stay cheap: importing agents.worker.main must NOT
#     drag the heavy pipeline (daily_batch / feed_assembly / ingestion) into the
#     import graph — those imports are lazy inside the handlers. We assert they are
#     absent from sys.modules right after importing main, so a future eager import
#     that slows cold-start fails loudly.
#   • The auth guard must be LIVE once mounted on the real app (not just on the SP1
#     throwaway app): an unauthenticated POST /pipeline/daily through the real app
#     must still 401.


def test_real_app_boots_and_serves_health(real_app_client: TestClient) -> None:
    """The real worker app constructs and GET /healthz returns 200 (boot smoke).

    TestClient(app) constructing without raising proves agents.worker.main imports
    and the app builds with the pipeline router mounted; /healthz proves the worker
    is live without touching any heavy dependency.
    """
    response = real_app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_real_app_openapi_exposes_both_pipeline_paths(
    real_app_client: TestClient,
) -> None:
    """Both pipeline routes appear in the REAL app's OpenAPI schema (mounted).

    The OpenAPI paths are the proof the router is registered on the deployed app —
    if include_router were dropped, neither path would be present and 7b/7c could
    not call them.
    """
    paths = real_app_client.app.openapi()["paths"]
    assert _DAILY_PATH in paths
    assert _ASSEMBLE_PATH in paths


def test_real_app_pipeline_daily_requires_auth(real_app_client: TestClient) -> None:
    """POST /pipeline/daily with NO token → 401 through the REAL app (guard is live).

    Proves the router's router-wide auth guard is active once mounted on the real
    worker — the seam is not accidentally left open by the mount.
    """
    response = real_app_client.post(_DAILY_PATH, json=_DAILY_BODY)
    assert response.status_code == 401


def test_importing_worker_main_does_not_eager_import_pipeline() -> None:
    """Importing agents.worker.main must NOT pull heavy pipeline modules in.

    The handlers import the pipeline lazily (inside the request body) so the worker
    cold-starts fast and /healthz stays cheap. We import main in a fresh subprocess
    (no pre-warmed sys.modules from other tests) and assert the heavy modules are
    absent from sys.modules — a regression that makes an import eager fails here.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import agents.worker.main; "
        "heavy = [m for m in "
        "('agents.pipeline.daily_batch', 'agents.pipeline.feed_assembly', "
        "'agents.ingestion.interest_keyed_pipeline') if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    eagerly_imported = result.stdout.strip()
    assert eagerly_imported == "", (
        f"agents.worker.main eagerly imported heavy pipeline modules: {eagerly_imported}"
    )


# ── /feed/assemble-mine — Phase 7b SP1: JWT-scoped single-user assemble ───────
#
# WHY (Rule 9 — encode the AUTH BOUNDARY, not the call shape):
#   • This is the ONLY pipeline route the browser/app may call directly, so it must
#     NOT use the shared PIPELINE_TRIGGER_SECRET (which can never ship to a client).
#     It authenticates with the CALLER's own Supabase access token. A request with
#     NO token or an INVALID/expired token MUST be 401 — if that ever passes, an
#     anonymous caller could write a feed.
#   • The user_id MUST come ONLY from the verified token, NEVER from the body. The
#     security failure this guards against is a caller passing someone else's
#     user_id to assemble (and read) that person's feed. So the test asserts
#     _assemble_for_user is called with the TOKEN's user_id, and that a body
#     carrying a different user_id is IGNORED (the model has no such field).
#   • A valid token reaches the SAME _assemble_for_user as the service-role route
#     and returns the SAME AssembleFeedResponse (200 + count).
#
# We mock at the BOUNDARY: the supabase-auth client builder (so auth.get_user is a
# fake returning a user) and _assemble_for_user (so no DB/network and we can assert
# the user_id it received) — never a real token, never a real Supabase call.


def _bearer(token: str) -> dict[str, str]:
    """Build a Bearer Authorization header carrying a Supabase access token."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def patched_assemble_mine(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch the JWT seam at the boundary: the auth client + _assemble_for_user.

    By default the fake ``auth.get_user(token)`` returns a user whose id is
    ``_TOKEN_USER_ID`` for ANY non-empty token, and ``_assemble_for_user`` is an
    AsyncMock returning ``(24, 30)``. Returns the mocks so tests can tune the auth
    response (e.g. raise for an invalid token) and assert the call args.
    """
    auth_namespace = SimpleNamespace(
        get_user=MagicMock(
            return_value=SimpleNamespace(
                user=SimpleNamespace(id=_TOKEN_USER_ID)
            )
        )
    )
    fake_auth_client = SimpleNamespace(auth=auth_namespace)
    monkeypatch.setattr(
        pipeline_routes, "_build_supabase_for_auth", lambda: fake_auth_client
    )

    assemble = AsyncMock(return_value=(24, 30))
    monkeypatch.setattr(pipeline_routes, "_assemble_for_user", assemble)

    return {
        "get_user": auth_namespace.get_user,
        "assemble": assemble,
    }


def test_assemble_mine_without_authorization_header_returns_401(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """No Authorization header → 401 (no anonymous feed writes on the client route)."""
    response = client.post(_ASSEMBLE_MINE_PATH, json={})
    assert response.status_code == 401
    patched_assemble_mine["assemble"].assert_not_called()  # type: ignore[attr-defined]


def test_assemble_mine_with_invalid_token_returns_401(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """An invalid/expired token (auth.get_user raises) → 401, assemble never runs."""
    patched_assemble_mine["get_user"].side_effect = Exception(  # type: ignore[attr-defined]
        "invalid JWT"
    )
    response = client.post(
        _ASSEMBLE_MINE_PATH, json={}, headers=_bearer("expired.or.bad.jwt")
    )
    assert response.status_code == 401
    patched_assemble_mine["assemble"].assert_not_called()  # type: ignore[attr-defined]


def test_assemble_mine_with_no_user_in_token_returns_401(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """auth.get_user returns no user → 401 (a token that resolves to nobody)."""
    patched_assemble_mine["get_user"].return_value = SimpleNamespace(user=None)  # type: ignore[attr-defined]
    response = client.post(
        _ASSEMBLE_MINE_PATH, json={}, headers=_bearer(_VALID_JWT)
    )
    assert response.status_code == 401
    patched_assemble_mine["assemble"].assert_not_called()  # type: ignore[attr-defined]


def test_assemble_mine_does_not_use_shared_pipeline_secret(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """The shared PIPELINE_TRIGGER_SECRET must NOT authenticate this route.

    Sending the service-role secret (not a Supabase JWT) makes the fake auth client
    treat it as the token; the route is exempt from the shared-secret guard, so the
    secret is verified as a JWT — proving the route does not honour the shared secret
    as its auth (it goes through the JWT dependency, which here happens to accept any
    non-empty token). The contract under test: the shared-secret guard is NOT what
    gates this route.
    """
    response = client.post(
        _ASSEMBLE_MINE_PATH, json={}, headers=_bearer(_EXPECTED_SECRET)
    )
    # Not a 401-from-the-shared-guard: the request reaches the JWT dependency.
    assert response.status_code == 200
    patched_assemble_mine["get_user"].assert_called_once_with(_EXPECTED_SECRET)  # type: ignore[attr-defined]


def test_assemble_mine_valid_jwt_assembles_token_users_feed(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """A valid JWT → 200 and _assemble_for_user is called with the TOKEN's user_id."""
    response = client.post(
        _ASSEMBLE_MINE_PATH,
        json={"feed_date": "2026-06-16"},
        headers=_bearer(_VALID_JWT),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allocated_count"] == 24
    assert body["feed_total"] == 30

    assemble = patched_assemble_mine["assemble"]
    assemble.assert_awaited_once()  # type: ignore[attr-defined]
    call_args = assemble.await_args  # type: ignore[attr-defined]
    # user_id is positional arg 0; it MUST be the token's user, not anything else.
    assert call_args.args[0] == _TOKEN_USER_ID
    assert call_args.args[1] == date(2026, 6, 16)


def test_assemble_mine_ignores_user_id_in_body(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """A body trying to pass a DIFFERENT user_id is ignored — token id wins.

    This is the core auth-boundary guarantee: a caller cannot assemble another
    user's feed by smuggling a user_id in the body. The body's user_id is not a
    model field, so it is dropped; _assemble_for_user still receives the token id.
    """
    response = client.post(
        _ASSEMBLE_MINE_PATH,
        json={"user_id": "someone-elses-id", "feed_date": "2026-06-16"},
        headers=_bearer(_VALID_JWT),
    )
    assert response.status_code == 200
    assemble = patched_assemble_mine["assemble"]
    assert assemble.await_args.args[0] == _TOKEN_USER_ID  # type: ignore[attr-defined]
    assert assemble.await_args.args[0] != "someone-elses-id"  # type: ignore[attr-defined]


def test_assemble_mine_defaults_feed_date_to_today_utc(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """An omitted feed_date defaults to today (UTC) — not a 422, not a None date."""
    from datetime import datetime, timezone

    response = client.post(_ASSEMBLE_MINE_PATH, json={}, headers=_bearer(_VALID_JWT))
    assert response.status_code == 200
    assemble = patched_assemble_mine["assemble"]
    passed_date = assemble.await_args.args[1]  # type: ignore[attr-defined]
    assert passed_date == datetime.now(timezone.utc).date()


def test_assemble_mine_unknown_user_returns_404(
    client: TestClient, patched_assemble_mine: dict[str, object]
) -> None:
    """A verified user with no interest profile (LookupError) → 404, not 500/200."""
    patched_assemble_mine["assemble"].side_effect = LookupError(  # type: ignore[attr-defined]
        "no profile"
    )
    response = client.post(_ASSEMBLE_MINE_PATH, json={}, headers=_bearer(_VALID_JWT))
    assert response.status_code == 404


def test_assemble_mine_is_exempt_from_shared_secret_on_real_app(
    real_app_client: TestClient,
) -> None:
    """On the REAL mounted app, /feed/assemble-mine with NO token → 401 (its OWN guard).

    Proves the JWT route is exempt from the router-wide shared-secret guard yet still
    closed: with no token it is rejected by its own verify_supabase_user dependency,
    not by the pipeline-secret guard. (A 401 here, like the secret guard, but routed
    through the JWT dependency — the path is reachable and gated.)
    """
    response = real_app_client.post(_ASSEMBLE_MINE_PATH, json={})
    assert response.status_code == 401
