# Phase 0 — Sub-phase 4 execution report: Python → Remotion handoff + render the 5 MP4s

**Status:** SUCCESS (code + unit test done). Render half of the DoD is **BLOCKED-ON-INPUT** (40 user-supplied stills not present yet) — NOT a code failure.
**Date:** 2026-05-29

---

## What was built

The Python → Remotion render seam for M0: assemble a per-digest `DigestManifest`
from the real SP1 audio + real SP2 captions + (user-supplied) stills, stage the
assets where Remotion can resolve them, and invoke `npx remotion render`.

- **`agents/m0/manifest_models.py` (NEW, 120 LoC)** — Pydantic v2 mirror of
  `remotion/src/manifest.ts` field-for-field: `KenBurns`, `Cut`, `CaptionWord`,
  `CaptionTrack`, `DigestManifest`, plus the locked geometry constants
  (`FPS=30`, `WIDTH=1080`, `HEIGHT=1920`, `CUT_COUNT=8`). Split out of the
  builder so each file stays < 500 LoC (CLAUDE.md).
- **`agents/m0/build_render_manifest.py` (NEW, 488 LoC)** — the pure builder.
  Per digest: ffprobe the audio → `total_frames = round(duration * FPS)` → tile
  8 cut `durationInFrames` (beat-weighted: short hook, long detail beat, short
  CTA) so the sum equals `total_frames` **exactly** → default Ken Burns per cut
  (even cuts zoom-in + upward pan, odd cuts zoom-out + alternating horizontal
  pan) → resolve the 8 ordered stills from `assets/m0/digest-<n>/cut-{1..8}.{jpg,jpeg,png}`
  → embed the SP2 caption JSON **verbatim** (re-validated against the contract)
  → `headlineText` from `digests_input`. Raises a typed `MissingStillsError`
  (carries `digest_id` + 1-based `missing_cuts` + `fix_suggestion`) when any of
  the 8 stills is absent. Emits one manifest JSON per digest to
  `agents/m0/output/manifests/digest-<n>.manifest.json`.
- **`agents/m0/render_all.py` (NEW, 381 LoC)** — the driver. Stages each digest's
  audio (→ `remotion/public/m0/digest-<n>/audio.mp3`) and 8 stills
  (→ `m0/digest-<n>/cut-<k>.<ext>`), rewrites the manifest's `audioSrc`/`imageSrc`
  to the **staticFile-relative** paths (confirmed against `Digest.tsx::resolveSrc`:
  relative → `staticFile()` from `public/`; absolute/http passed through), writes
  the staged manifest, then invokes `npx remotion render src/index.ts Digest
  --props=<manifest> <out.mp4>`. Supports `--dry-run` (prints the per-digest
  frame plan + render command, touches nothing) and `--digest <id>` (single
  digest). Missing stills are reported as BLOCKED-ON-INPUT (exact expected paths)
  and skipped; a fully-blocked real run exits non-zero (Rule 12 — not "success").
- **`agents/pipeline/stages/tts_handoff.py` (REWRITTEN from TLDW donor PATTERN,
  126 LoC)** — kept the donor's shape (ffprobe-the-real-audio + assemble a typed
  handoff package) but rewrote the body for our seam: `build_render_handoff(digest)`
  returns a `RenderHandoff` (the `DigestManifest` + output MP4 path + render argv).
  Pure assembly; no staging / no render side effects (those live in `render_all`).
- **`tests/agents/m0/test_build_render_manifest.py` (NEW, 274 LoC)** +
  `tests/agents/m0/__init__.py` — 7 offline tests (temp fixture: 8 dummy stills +
  dummy audio + hand-written caption JSON; ffprobe monkeypatched).

## Divergences from the brief

1. **Models split into `manifest_models.py`.** The brief named
   `build_render_manifest.py` as the only new builder file, but the combined
   builder + 5 contract models was 577 LoC — over the 500-LoC agent-file limit
   (CLAUDE.md / `reference/conventions.md`). Split by responsibility (contract
   shape vs. assembly logic). Both files re-export cleanly; no behaviour change.
2. **`tts_handoff` produces the handoff package, does not invoke the render.**
   The render invocation lives in `render_all.py` (the brief's designated render
   driver). `tts_handoff` mirrors the donor's "build + return a handoff" role and
   keeps the render side effects in one place. Flagged for transparency.

## Self-review findings + fixes (Step B/C)

- **[HIGH — fixed] Dry-run was uninformative when stills are missing.** Initially
  `render_one_digest` built the full manifest first, so a no-stills digest raised
  before printing any plan. Refactored: `_print_dry_run_plan` computes the frame
  tiling from audio alone (ffprobe + `tile_cut_durations`, no stills needed),
  prints the plan, *then* reports the stills status. Dry-run now shows the full
  per-digest frame plan even in today's fully-blocked state.
- **[MED — fixed] File over 500 LoC** — see divergence #1.
- **[LOW — accepted] Caption re-validation cost.** `load_caption_track`
  re-validates the SP2 JSON against the mirrored `CaptionTrack` on every build.
  Cheap (a few hundred words) and it fails loud if SP2's shape ever drifts from
  the contract — kept deliberately.

## Validation (Step D)

- `ruff check agents/ tests/` → **All checks passed.**
- `ruff format --check agents/ tests/` → **28 files already formatted.**
- `pytest tests/agents/m0/test_build_render_manifest.py -v` → **7 passed.**
  - manifest validates against the contract (8 cuts, caption words embedded
    verbatim incl. highlight flags, non-empty headlineText, resolvable audioSrc,
    fps/width/height locked)
  - `sum(cuts[].durationInFrames) == round(audio_duration_s * fps)`
  - raises `MissingStillsError` on one missing still (names the cut)
  - raises with all 8 listed when the folder is empty
  - `tile_cut_durations` exact + 8-positive across 45/47.49/50.611/55.891/70s
  - rejects < 8 total frames; Ken Burns alternates in/out
- **Full suite** `pytest -q` → **30 passed** (SP1 + SP2 tests still green).
- `python -m agents.m0.render_all --dry-run` → runs, prints all 5 per-digest
  plans, reports each BLOCKED-ON-INPUT with exact expected paths, exits 0.
  Tiled frames (sum verified == round(dur*30)):
  - digest-1: 1518 `[108,181,217,235,235,217,181,144]` (50.61s)
  - digest-2: 1380 `[98,164,198,214,214,197,164,131]` (46.00s)
  - digest-3: 1677 `[119,200,240,260,260,240,199,159]` (55.89s)
  - digest-4: 1425 `[101,170,204,221,221,204,169,135]` (47.49s)
  - digest-5: 1421 `[101,169,204,220,220,203,169,135]` (47.37s)
- **Real 5-digest render:** SKIPPED — the 40 stills are not supplied. No fake
  MP4s produced.

## End-to-end wiring proof (Step F optional — DONE)

To de-risk the staticFile staging without the real stills, I staged the SP3
placeholder PNGs (`remotion/public/fixtures/assets/cut-{1..8}.png`) as throwaway
stand-in stills for **digest-1 only** and ran a real
`python -m agents.m0.render_all --digest digest-1`.

- The render **succeeded end-to-end**: assets staged → manifest rewritten to
  staticFile-relative paths → `npx remotion render` ran → MP4 produced.
- `ffprobe` on the throwaway MP4: **1080×1920, video + audio streams present** —
  proves the staticFile audio/image staging + Ken Burns + audio wiring all work.
- Throwaway artifacts deleted afterward: `agents/m0/output/video/digest-1.mp4`,
  `remotion/public/m0/`, `assets/m0/digest-1/`. No fake MP4 remains.

## Concern surfaced by the proof render (Rule 12 — cross-sub-phase gap)

**The rendered MP4 was 1650 frames / 55.02s, NOT digest-1's tiled 1518 frames /
50.6s.** Root cause: `remotion/src/Root.tsx` (SP3) sets the composition's
`durationInFrames` **statically** from `fixtures/sample-manifest.json`'s cut sum
(1650) at registration time. Remotion `--props` overrides the component props but
**not** the registered `durationInFrames`, and SP3's `Root.tsx` has no
`calculateMetadata` to derive duration from the incoming props. So every real
render would be 1650 frames regardless of the digest's true audio length — e.g.
digest-2 (46.0s) would get ~9.7s of black/frozen tail; digest-3 (55.9s) would be
clipped ~0.35s. That fails the phase DoD's "ffprobe duration 45–70s matching the
audio" for several digests.

**This is an SP3 fix, out of SP4's file scope** (I must not edit SP3 source). The
fix: add `calculateMetadata` to the `<Composition>` in `remotion/src/Root.tsx` so
`durationInFrames = sum(props.cuts[].durationInFrames)` is derived per-render from
the passed manifest. SP4's manifests already carry the correct per-digest tiling,
so once SP3 derives duration from props, the render lengths will be exact with no
SP4 change. **Recommend a small SP3 follow-up sub-phase / fix before the real
render.**

## Definition of done

- **Code + unit test: PASS.** Manifest builder, render driver, handoff stage, and
  the 7-case unit test all complete and green; ruff clean; end-to-end Remotion
  wiring proven with a throwaway one-digest render.
- **Render (5 ffprobe-verified MP4s): BLOCKED-ON-INPUT.** The 40 stills are not
  supplied. Drop them at:
  `assets/m0/digest-{1..5}/cut-{1..8}.{jpg|png}` (8 per digest, ordered;
  prompts + house style are in `documents/m0-digests.md`).
  **Resume command:** `python -m agents.m0.render_all`
  (preview the plan first with `python -m agents.m0.render_all --dry-run`).
- **Blocker to clear before that render produces correct durations:** the SP3
  `Root.tsx` `calculateMetadata` fix described above (otherwise all 5 render at a
  fixed 55s instead of their real lengths).

## Files touched

- `agents/m0/manifest_models.py` (NEW)
- `agents/m0/build_render_manifest.py` (NEW)
- `agents/m0/render_all.py` (NEW)
- `agents/pipeline/stages/tts_handoff.py` (REWRITTEN from donor PATTERN)
- `tests/agents/m0/test_build_render_manifest.py` (NEW)
- `tests/agents/m0/__init__.py` (NEW — test plumbing)
- Created (empty, gitignore-friendly output/staging dirs):
  `agents/m0/output/{manifests,video}/`, `assets/m0/`
