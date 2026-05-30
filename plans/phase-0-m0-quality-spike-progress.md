# Progress: phase-0-m0-quality-spike

**Phase file:** plans/phase-0-m0-quality-spike.md
**Started:** 2026-05-29
**Mode:** sequential (skill default; SP1/SP3 are independent but not run in parallel)

## Resolved blockers / decisions
- GEMINI_API_KEY: SET in `.env` (SP1 renders real audio). OK.
- Captions (SP2): per user, NO Whisper / NO OPENAI_API_KEY. Build caption track from the
  known transcript (`digests_input.py`) time-sliced across real per-turn audio durations from SP1.
  Deviation from donor `forced_alignment.py` (Whisper port) — flagged per Rule 12.
- 40 stills at `assets/m0/digest-{1..5}/cut-{1..8}.{jpg,png}`: STILL MISSING.
  → SP4 code + unit test can be built now; SP4's real MP4 render + the phase commit are BLOCKED until stills land.
- Python: default `python3` is 3.10.6; use `python3.12` (3.12.13 present) in a `.venv`.

## Sub-phase progress
- [x] 1: Port TTS spine + render 5 digests' audio — COMPLETE (SUCCESS, DoD PASS). 5 mp3s @ agents/m0/output/audio/digest-{1..5}.mp3; durations d1=50.61 d2=46.00 d3=55.89 d4=47.49 d5=47.37; mocked tests 7 pass; Ruff clean. Report: sub-1.md
- [x] 2: Transcript→time-sliced word-by-word caption tracks — COMPLETE (SUCCESS, DoD PASS). 5 caption JSONs @ agents/m0/output/captions/digest-{1..5}.captions.json; 16 tests pass (23 total); word counts match; 1 highlight/sentence; Whisper→time-slice deviation documented. forced_alignment.py=519 LoC (soft >500 flag, revisit in slop scan). Report: sub-2.md
- [x] 3: Remotion 9:16 digest compositor — COMPLETE (SUCCESS, DoD PASS). remotion/ project; Digest @1080x1920/30fps/1650f; captionWordsAtFrame 6 tests pass; smoke stills confirm headline card + yellow-highlight caption + Ken Burns. manifest.ts = DigestManifest contract (captionTrack matches SP2 verbatim). Report: sub-3.md
- [x] 4: Python→Remotion handoff + render 5 MP4s — CODE COMPLETE (SUCCESS). build_render_manifest + render_all + manifest_models + tts_handoff + test (7 pass); full suite 30/30; --dry-run prints 5 plans + BLOCKED-ON-INPUT; render wiring PROVEN via throwaway 1-digest render. Report: sub-4.md. **Real 5-MP4 render BLOCKED on missing 40 stills.**

### Integration bug found by SP4 (fix in SP3 scope) — RESOLVED ✅
SP3 Root.tsx registered static durationInFrames=1650 → all digests would render fixed 55s regardless of audio. FIXED: added calculateMetadata deriving durationInFrames=sum(props.cuts[].durationInFrames) + fps from props; static values kept as no-props fallback. Proof: no-props=1650/55s; props(cuts=900)=900/30s. tsc clean, vitest 6/6.

## Phase-level quality passes (run on the built code; render still pending)
- Slop scan: PASS (3 accepted notes: forced_alignment 519 LoC cohesive; render_all print()s are CLI UX; 2 scoped type:ignore on SDK boundary).
- CSO: PASS (.env gitignored — key safe; subprocess list-form no injection; deps clean; SecretStr, no key logged; no new auth/input surface).

## STATUS: 3.75 / 4 sub-phases at full DoD. Phase-level DoD (5 MP4s) was BLOCKED-ON-INPUT (40 stills).
All code complete, tested (Py 30/30, vitest 6/6), quality-passed.

## ⚠ MID-PHASE PIVOT (user decision, 2026-05-29) — Rule 7 conflict resolved toward newer direction
reference/poster-pipeline.md (today, "single source of truth") pivoted the product to ONE poster-grade image per story.
The built 8-cut pipeline + documents/m0-digests.md are the OLD direction. User chose: **PIVOT M0 to one poster/story.**
Unblock images via Gemini Nano Banana Pro (model `gemini-3-pro-image-preview`) instead of hand-dropping 40 stills.
Canvas reference: ~/Canvas/Canvas/canvas/src/lib/services/gemini-service.ts (generateContent + responseModalities:[TEXT,IMAGE] + imageConfig{aspectRatio,imageSize}; parse candidates[0].content.parts[].inlineData).
TODO reconciliation (poster-pipeline §11): m0-digests.md + phase-0 plan still say "8-cut locked" — flag in final report.

### New work plan (post-pivot)
- [x] 5: Poster generator + 5 concept prompts BUILT + RUN (agents/m0/{poster_prompts,generate_posters}.py). Billing enabled → re-ran `gemini-3-pro-image-preview` on 2026-05-29. All 5 posters generated on the PRIMARY prompt (no safety refusal, no fallback), HTTP 200 each, at `assets/m0/digest-{1..5}/poster.png`. Visual check: all on-brief (one idea/object, one accent, ~70% dark, reserved lower band, textless). Two notes carried below.
- [x] 3-rework: SP3 Digest → single full-frame poster + brief headline intro + gentle static-first drift; manifest single-poster (posterSrc, durationInFrames, optional kenBurns); calculateMetadata from props.durationInFrames; captionWordsAtFrame unchanged; tsc clean, vitest 6/6; smoke stills confirm. (7 unused cut-2..8.png placeholders linger → sweep in slop scan.)
- [x] 4-rework: SP4 manifest_models/build_render_manifest/render_all/test → single poster (posterSrc, durationInFrames=round(dur*fps)). Suite 28/28, ruff clean. Wiring PROVEN: stand-in poster → digest-1 render 1518/1518f, 1080x1920, video+audio, 50.645s≈audio. Dry-run frames: 1518/1380/1677/1425/1421 (50.6/46.0/55.9/47.5/47.4s, all in 45–70). SP4 report needs a rework-note added at commit prep.
- [ ] Render 5 MP4s → ffprobe-verify (1080x1920, 45–70s, audio) → final slop/CSO sweep (incl. delete 7 unused remotion cut-2..8.png placeholders + SP4 report note) → commit.

### Billing blocker CLEARED (2026-05-29). 5 posters generated. Remaining: render → ffprobe-verify → commit.
Next: `.venv/bin/python -m agents.m0.render_all` → 5 MP4s → ffprobe-verify (1080×1920, 45–70s, audio) → final slop/CSO sweep → commit.
ALL code (SP1–SP5 + reworks) is built + tested; nothing else gates the phase.

### ⚠ Two technical notes on the generated posters (carry to commit / render):
1. **JPEG bytes in a `.png` filename.** Nano Banana Pro returns `image/jpeg`; the driver writes raw bytes to `poster.png` regardless. Files are valid JPEGs (768×1376) just mis-extensioned. Remotion/Chromium content-sniffs so the render likely works, but `_read_png_dimensions` can't verify JPEG (logs `NonexNone`). Decide before commit: rename to `.jpg` (ripples into build_render_manifest poster glob) OR accept mislabel.
2. **Size 768×1376 (1K 9:16), not the pipeline target 1080×1920.** Driver doesn't set `image_config.image_size`. Fine as a Remotion background (scales ~1.4×); add `image_size="2K"` to `_generate_one_call` for crisper output if desired.

### SP3 manifest.ts contract (SP4 populates)
DigestManifest = { digest_id:str, audioSrc(.mp3), headlineText:str, cuts:Cut[8], captionTrack(=SP2 JSON verbatim), fps, width:1080, height:1920 }
Cut = { imageSrc, durationInFrames, kenBurns{startScale,endScale,startTranslateX,endTranslateX,startTranslateY,endTranslateY} }
- SP4 must tile 8 cut durationInFrames to real ffprobe audio length (sum of cut frames = composition duration).
- Remotion staticFile() resolves only from remotion/public/ → SP4 stages real audio+stills into remotion/public/ and references via relative paths (resolveSrc passes http/abs through). Caption track is embedded JSON (no file copy).
- headlineText from digests_input.DIGESTS[i].headline.

### SP2 caption-track contract (SP3 CaptionTrack + captionWordsAtFrame + manifest.ts MUST match)
Per-digest JSON: { digest_id:str, audio_duration_s:num, speech_end_s:num, sentence_count:int,
words:[ {word:str(verbatim, casing+punctuation), start_s, end_s, sentence_index:int, is_highlight:bool} ] }
Invariants: words contiguous (w[i].end_s==w[i+1].start_s), monotonic, all in [0, speech_end_s];
speech_end_s<=audio_duration_s (only digest-2 differs 44.93 vs 46.0); exactly one is_highlight:true per sentence_index.

### Carry-forward facts from SP1
- Audio = `.mp3` (SP4 manifest points at .mp3).
- TTS = ONE multi-speaker call per digest → ONE audio segment per digest (total duration only, NO per-turn boundaries). SP2 time-slices transcript across total duration.
- digest-2 has ~1.07s trailing padded silence (caption ~44.93s, not full 46.00s).
- GEMINI free-tier quota (10 TTS/day) EXHAUSTED this run → SP2/SP4 must REUSE on-disk audio, never re-render (429 until ~24h reset).
- Output mp3s NOT gitignored (~1MB each) — commit decision deferred to phase end.
- [ ] 3: Remotion 9:16 digest compositor — PENDING
- [ ] 4: Python→Remotion handoff + render 5 MP4s — PENDING (render gated on stills)

## Phase commit
- NOT YET. Requires all 4 sub-phase DoDs + phase-level DoD (5 ffprobe-verified MP4s) to pass.
  Blocked on the 40 stills for SP4's render.
