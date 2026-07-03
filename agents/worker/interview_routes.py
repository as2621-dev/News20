"""Interview HTTP seam: ``POST /api/interview/turn`` (FSR interview slice #1).

Mounts one route that drives the conversational onboarding interview, one turn per
request. Auth mirrors ``/feed/assemble-mine`` exactly — it reuses
:func:`agents.worker.pipeline_routes.verify_supabase_user`, so the caller's own Supabase
JWT gates the endpoint and the identity comes from the verified token, never the body.

Failure contract (spec §2): EVERY internal failure returns HTTP 200 with a typed ``retry``
body so the chat never dead-ends. Only auth (401) and the shared rate-limit middleware
(429) short-circuit before the handler. The Gemini key never leaves the worker — the
engine calls Gemini server-side via :class:`~agents.pipeline.llm_clients.LLMClient`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agents.interview.engine import run_interview_turn
from agents.interview.models import InterviewTurnRequest, InterviewTurnResponse
from agents.pipeline.llm_clients import LLMClient
from agents.shared.logger import get_logger
from agents.worker.pipeline_routes import verify_supabase_user

logger = get_logger("worker.interview")

# Reason: apply the JWT guard router-wide (fail-closed) so no future route added to this
# file is unauthenticated by omission — mirroring the pipeline router's invariant. The
# per-route dependency below is deduped by FastAPI within a request, so it runs once.
router = APIRouter(dependencies=[Depends(verify_supabase_user)])


@router.post("/api/interview/turn", response_model=InterviewTurnResponse)
async def post_interview_turn(
    request: InterviewTurnRequest,
    verified_user_id: str = Depends(verify_supabase_user),
) -> InterviewTurnResponse:
    """Run one onboarding interview turn for the authenticated user.

    Delegates all logic to :func:`agents.interview.engine.run_interview_turn`, which never
    raises — turn 1 returns the fixed roots (no LLM), deeper turns call Gemini for the next
    question or the terminal micro-interest list, and any failure yields a typed ``retry``
    body (still HTTP 200). The verified user id gates the call but is not part of the
    stateless turn contract (state travels in the request body).

    Args:
        request: The interview turn request (the whole conversation state).
        verified_user_id: The caller's Supabase user id (from the verified JWT).

    Returns:
        The typed :class:`InterviewTurnResponse` (question / terminal / retry).
    """
    logger.info(
        "interview_route_received",
        user_id=verified_user_id,
        prior_exchanges=len(request.conversation_state),
    )
    llm_client = LLMClient()
    return await run_interview_turn(request, llm_client)
