"""One-off cost test: regenerate the 5 M0 posters with Gemini FLASH image and
build a side-by-side HTML (Nano Banana PRO left, FLASH right) for quality review.

Read-only w.r.t. the Pro outputs; writes Flash PNGs to assets/m0/flash-compare/
and an HTML to assets/m0/flash-compare/compare.html.

    .venv/bin/python scripts/flash_vs_pro_compare.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from google import genai
from google.genai import types

from agents.m0.generate_posters import (
    ASSETS_M0_DIR,
    POSTER_ASPECT_RATIO,
    _extract_image_bytes,
)
from agents.m0.poster_prompts import POSTER_PROMPTS
from agents.shared.settings import Settings

# Flash image counterpart to gemini-3-pro-image-preview (Nano Banana Pro).
FLASH_IMAGE_MODEL = "gemini-3.1-flash-image-preview"
OUT_DIR = ASSETS_M0_DIR / "flash-compare"


def _generate_flash(client: genai.Client, prompt_text: str) -> tuple[bytes, str]:
    """Call the Flash image model with the SAME prompt + aspect ratio as Pro."""
    response = client.models.generate_content(
        model=FLASH_IMAGE_MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=prompt_text)])],
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=POSTER_ASPECT_RATIO),
        ),
    )
    return _extract_image_bytes(response)


def main() -> None:
    api_key = Settings().gemini_api_key.get_secret_value()
    if not api_key:
        print("GEMINI_API_KEY missing in .env")
        sys.exit(1)
    client = genai.Client(api_key=api_key)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    for poster in POSTER_PROMPTS:
        n = poster.digest_id
        pro_path = ASSETS_M0_DIR / n / "poster.png"
        flash_path = OUT_DIR / f"{n}.png"
        status = "ok"
        try:
            image_bytes, mime = _generate_flash(client, poster.prompt_text)
            if not image_bytes:
                status = "FLASH returned no image part (safety filter / empty)"
            else:
                flash_path.write_bytes(image_bytes)
                print(f"{n}: flash {len(image_bytes)} bytes -> {flash_path}")
        except Exception as exc:  # noqa: BLE001 — record verbatim for the cost test
            status = f"{type(exc).__name__}: {exc}"
            print(f"{n}: ERROR {status}")
        rows.append(
            {
                "id": n,
                "archetype": poster.archetype,
                "pro": os.path.relpath(pro_path, OUT_DIR) if pro_path.exists() else "",
                "flash": os.path.relpath(flash_path, OUT_DIR) if flash_path.exists() else "",
                "status": status,
            }
        )
        time.sleep(2.0)

    _write_html(rows)


def _write_html(rows: list[dict[str, str]]) -> None:
    cards = []
    for r in rows:
        pro_img = f'<img src="{r["pro"]}" alt="pro">' if r["pro"] else '<div class="empty">no pro file</div>'
        flash_img = (
            f'<img src="{r["flash"]}" alt="flash">' if r["flash"] else f'<div class="empty">{r["status"]}</div>'
        )
        cards.append(
            f"""
      <section class="row">
        <h2>{r['id']} &middot; {r['archetype']}</h2>
        <div class="pair">
          <figure><figcaption>Nano Banana PRO (gemini-3-pro-image-preview)</figcaption>{pro_img}</figure>
          <figure><figcaption>FLASH (gemini-3.1-flash-image-preview)</figcaption>{flash_img}</figure>
        </div>
        <p class="status">{r['status']}</p>
      </section>"""
        )
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Pro vs Flash poster comparison</title>
<style>
  body {{ background:#0b0b0d; color:#e7e7ea; font-family:-apple-system,system-ui,sans-serif; margin:0; padding:32px; }}
  h1 {{ font-size:22px; }}
  .legend {{ color:#9aa; margin-bottom:24px; }}
  .row {{ margin:0 0 48px; border-top:1px solid #222; padding-top:16px; }}
  .row h2 {{ font-size:15px; color:#cbd5e1; }}
  .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  figure {{ margin:0; }}
  figcaption {{ font-size:12px; color:#9aa; margin-bottom:8px; }}
  img {{ width:100%; border-radius:10px; display:block; background:#111; }}
  .empty {{ aspect-ratio:9/16; display:flex; align-items:center; justify-content:center;
            background:#171717; border:1px dashed #444; border-radius:10px; color:#f87171;
            text-align:center; padding:16px; font-size:13px; }}
  .status {{ font-size:12px; color:#888; }}
</style></head>
<body>
  <h1>Poster quality: Nano Banana Pro vs Flash</h1>
  <div class="legend">Same prompts, same 9:16 aspect. LEFT = Pro (current). RIGHT = Flash (cheaper candidate).</div>
  {''.join(cards)}
</body></html>"""
    out = OUT_DIR / "compare.html"
    out.write_text(html)
    print(f"\nHTML -> {out}")


if __name__ == "__main__":
    main()
