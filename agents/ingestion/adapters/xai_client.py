"""Shared xAI / Grok Agent Tools (``/v1/responses``) client seam.

A tiny, source-agnostic helper for the two xAI callers in the ingestion layer: the
per-handle X adapter (``x_account.py``) and the shared X cluster sweep
(``cluster_sweep.py``). It owns the request/error contract in ONE place so a change
to the endpoint, auth handling, or status-code mapping cannot silently drift between
callers — and so a caller's failure logs under ITS OWN ``source_name``, not another
adapter's.

Every function takes a ``source_name`` (e.g. ``"x_cluster_sweep"``) so structured
logs and :class:`AdapterFetchError` are attributed to the real caller. The
``XAI_API_KEY`` is read only inside :func:`post_xai`, sent ONLY in the Authorization
header, and is never logged.

(``x_account.py`` predates this module and keeps its own equivalent internals for
now; adopting this client there is a follow-up refactor tracked separately — see
docs/residual-review-findings/issue-23.md.)
"""

from __future__ import annotations

import json
from typing import Any

from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"
XAI_MODEL = (
    "grok-4.3"  # Agent Tools API model (server-side x_search); see /v1/responses
)


async def post_xai(
    settings: Any,
    prompt: str,
    *,
    source_name: str,
    tools: list[dict[str, Any]] | None = None,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """POST a single prompt to xAI ``/v1/responses`` and return the parsed JSON body.

    Reads ``settings.xai_api_key`` at call time (never logged). Raises
    :class:`AdapterFetchError` — attributed to ``source_name`` — on a missing key or
    any HTTP / transport error, so the caller can degrade to an empty result + a loud
    log with the seam intact.

    Args:
        settings: A Settings object exposing ``xai_api_key`` (a SecretStr).
        prompt: The user-role prompt text.
        source_name: The caller's structured-log / error attribution (e.g. the sweep
            or adapter name).
        tools: Optional Agent-Tools tool specs (e.g. an ``x_search`` tool); omitted
            from the payload when None (a plain chat call).
        timeout_seconds: HTTP timeout for the call.

    Returns:
        The parsed xAI JSON response body.

    Raises:
        AdapterFetchError: When the key is unset or the call rate-limits / errors /
            returns a non-JSON body (no key leaked).
    """
    import httpx  # noqa: PLC0415 — local import keeps the network boundary explicit

    api_key = settings.xai_api_key.get_secret_value().strip()
    if not api_key:
        raise AdapterFetchError(
            message="XAI_API_KEY is not set",
            adapter_name=source_name,
            fix_suggestion="Set XAI_API_KEY in the worker env (it is in .env); the xAI "
            "Agent Tools (/v1/responses + x_search) call needs it.",
        )

    payload: dict[str, Any] = {
        "model": XAI_MODEL,
        "input": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{XAI_BASE_URL}/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        # Reason: surface auth (401/403) and rate-limit (429) distinctly, but NEVER
        # include the request headers / key in the log or error.
        status_code = exc.response.status_code
        raise AdapterFetchError(
            message=f"xAI HTTP {status_code}",
            adapter_name=source_name,
            fix_suggestion=(
                "xAI auth failed — verify XAI_API_KEY is valid and authorized"
                if status_code in (401, 403)
                else "xAI rate-limited — back off and retry"
                if status_code == 429
                else "xAI returned an HTTP error; check api.x.ai status and that the "
                "Agent Tools (/v1/responses + x_search) API is enabled"
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 — normalize all transport errors
        raise AdapterFetchError(
            message="xAI call failed",
            adapter_name=source_name,
            fix_suggestion="xAI call errored (network/timeout/parse); retry later",
        ) from exc


def parse_xai_response(
    body: dict[str, Any], *, source_name: str
) -> list[dict[str, Any]]:
    """Pull a JSON-array-of-objects payload out of an xAI response, or ``[]``.

    The assistant text is expected to be a JSON array string (per the caller's
    prompt). Tolerates a ```json fence and both API shapes; a non-array / unparseable
    payload yields ``[]`` (logged under ``source_name``), never a crash.

    Args:
        body: The parsed xAI JSON response (``/v1/responses`` or chat-completions).
        source_name: The caller's structured-log attribution.

    Returns:
        The list of dict elements, or an empty list when none could be parsed.
    """
    content = _extract_assistant_text(body)
    if content is None:
        logger.warning(
            "xai_no_content",
            source=source_name,
            fix_suggestion="xAI response had no message content; treated as empty",
        )
        return []

    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "xai_unparseable",
            source=source_name,
            content_preview=text[:160],
            fix_suggestion="xAI did not return a JSON array; treated as empty",
        )
        return []

    if not isinstance(parsed, list):
        logger.warning(
            "xai_not_array",
            source=source_name,
            fix_suggestion="xAI JSON was not an array; treated as empty",
        )
        return []

    return [item for item in parsed if isinstance(item, dict)]


def _extract_assistant_text(body: dict[str, Any]) -> str | None:
    """Pull the assistant text out of an xAI response, across both API shapes.

    Primary: the Agent Tools ``/v1/responses`` shape — ``body["output"]`` is a list
    whose ``type == "message"`` item carries a ``content`` list with a
    ``type == "output_text"`` object holding ``text``. Fallback: the legacy
    chat-completions shape ``body["choices"][0]["message"]["content"]``.

    Args:
        body: The parsed xAI JSON response.

    Returns:
        The assistant text, or None when neither shape is present.
    """
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    return content.get("text")

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
