"""Probe: does the Gemini Batch API support Nano Banana Pro image generation?

Submits ONE inline batch job with two requests against
``gemini-3-pro-image-preview``:

  * request ``text_only``       — text prompt -> image (simplest shape)
  * request ``image_conditioned`` — reference image + text prompt -> image
    (the exact shape ``agents.m0.generate_posters.generate_from_reference`` uses)

Polls to a terminal state and reports whether each request returned an image
part. Gates the whole batch-poster integration. Writes any returned images to
``/tmp/batch_probe/`` and prints ``PROBE_RESULT: ...`` lines for the caller.

    .venv/bin/python scripts/probe_batch_image.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv  # noqa: E402
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

IMAGE_MODEL = "gemini-3-pro-image-preview"
ASPECT_RATIO = "9:16"
IMAGE_SIZE = "2K"
OUT_DIR = Path("/tmp/batch_probe")

TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}

TEXT_PROMPT = (
    "A bold editorial news poster, 9:16 vertical, dramatic cinematic lighting, "
    "a single semiconductor wafer glowing on dark slate, no text, no logos."
)
RECAST_PROMPT = (
    "Recompose this reference into a bold 9:16 editorial news poster: dramatic "
    "cinematic lighting, high contrast, single clear subject, no text, no logos."
)


def _find_reference_image() -> tuple[bytes, str]:
    """Return (bytes, mime) of an existing poster PNG to use as the seed."""
    for path in sorted((Path(_REPO_ROOT) / "assets" / "m0").glob("*/poster.png")):
        return path.read_bytes(), "image/png"
    raise SystemExit("PROBE_RESULT: FAIL no reference poster.png found in assets/m0")


def _image_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=ASPECT_RATIO, image_size=IMAGE_SIZE),
    )


def _has_image(response: object) -> tuple[bool, int]:
    """Return (found_image, byte_len) by scanning candidates[0].content.parts."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return False, 0
    content = getattr(candidates[0], "content", None)
    parts = (getattr(content, "parts", None) if content else None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is not None and getattr(inline, "data", None):
            data = inline.data
            return True, len(data) if isinstance(data, (bytes, bytearray)) else len(str(data))
    return False, 0


def main() -> None:
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("PROBE_RESULT: FAIL GEMINI_API_KEY missing")
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key)

    ref_bytes, ref_mime = _find_reference_image()
    print(f"reference image: {len(ref_bytes)} bytes ({ref_mime})", flush=True)

    requests = [
        types.InlinedRequest(
            contents=[types.Content(role="user", parts=[types.Part(text=TEXT_PROMPT)])],
            config=_image_config(),
            metadata={"key": "text_only"},
        ),
        types.InlinedRequest(
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime),
                        types.Part(text=RECAST_PROMPT),
                    ],
                )
            ],
            config=_image_config(),
            metadata={"key": "image_conditioned"},
        ),
    ]

    try:
        job = client.batches.create(
            model=IMAGE_MODEL,
            src=requests,
            config=types.CreateBatchJobConfig(display_name="probe-nano-banana-pro-batch"),
        )
    except Exception as exc:  # noqa: BLE001 — verbatim for the probe
        print(f"PROBE_RESULT: FAIL create raised {type(exc).__name__}: {exc}", flush=True)
        sys.exit(2)

    job_name = job.name
    print(f"submitted batch job: {job_name} (state={getattr(job.state,'name',job.state)})", flush=True)

    deadline = time.time() + 45 * 60
    state_name = ""
    while time.time() < deadline:
        time.sleep(20)
        try:
            job = client.batches.get(name=job_name)
        except Exception as exc:  # noqa: BLE001
            print(f"  poll error (continuing): {type(exc).__name__}: {exc}", flush=True)
            continue
        state_name = getattr(job.state, "name", str(job.state))
        print(f"  poll: state={state_name}", flush=True)
        if state_name in TERMINAL_STATES:
            break

    if state_name != "JOB_STATE_SUCCEEDED":
        err = getattr(job, "error", None)
        print(f"PROBE_RESULT: FAIL terminal_state={state_name} error={err}", flush=True)
        sys.exit(3)

    dest = getattr(job, "dest", None)
    inlined = getattr(dest, "inlined_responses", None) if dest else None
    if not inlined:
        print(f"PROBE_RESULT: FAIL no inlined_responses on dest={dest!r}", flush=True)
        sys.exit(4)

    ok_count = 0
    for index, item in enumerate(inlined):
        response = getattr(item, "response", None)
        item_error = getattr(item, "error", None)
        if response is None:
            print(f"  response[{index}]: error={item_error}", flush=True)
            continue
        found, blen = _has_image(response)
        label = f"req{index}"
        if found:
            ok_count += 1
            # decode + write
            cand = response.candidates[0]
            for part in cand.content.parts:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    out = OUT_DIR / f"probe_{index}.png"
                    out.write_bytes(inline.data)
                    print(f"  {label}: IMAGE {blen} bytes -> {out}", flush=True)
                    break
        else:
            print(f"  {label}: NO IMAGE (error={item_error})", flush=True)

    if ok_count == len(inlined):
        print(f"PROBE_RESULT: SUCCESS all {ok_count}/{len(inlined)} requests returned images", flush=True)
        sys.exit(0)
    print(f"PROBE_RESULT: PARTIAL {ok_count}/{len(inlined)} returned images", flush=True)
    sys.exit(5)


if __name__ == "__main__":
    main()
