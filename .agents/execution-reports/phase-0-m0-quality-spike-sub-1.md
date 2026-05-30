# Phase 0 — Sub-phase 1 execution report: TTS spine + 5 real digest audios

**Status:** SUCCESS
**Date:** 2026-05-29

## What was implemented
Ported the TLDW TTS spine into `agents/` and rendered all 5 M0 digests to real
anchor-duo audio via Gemini multi-speaker TTS (ALEX=`Leda`, JORDAN=`Sadaltager`,
model `gemini-2.5-flash-preview-tts`, multi-speaker single call, chunked on
`<Person[12]>` boundaries under a ~4000-byte budget).

- **Shared infra (PORT):** `logger.py` verbatim; `settings.py` trimmed to the
  Gemini-key surface (Pinecone/OpenAI/Supabase required fields dropped —
  `extra="ignore"` so the shared `.env` still loads); `exceptions.py` trimmed to
  `VoiceAgentError → PipelineStageError → TTSRenderError`.
- **Voice (PORT):** `models.py` (`DialogueTurn` + `EpisodeConfig` +
  `SegmentTiming` + `AssembledEpisode`); `gemini_tts.py` (chunking + voice map
  kept verbatim); `audio.py` (`assemble_episode` + inter-speaker gaps +
  `export_digest_audio`).
- **M0:** `digests_input.py` (5 scripts transcribed by hand from
  `documents/m0-digests.md` as typed `Digest` + `DIGESTS`, reusable by SP2/SP4);
  `render_audio.py` driver (`python -m agents.m0.render_audio`).
- **Plumbing:** `requirements.txt` (yt-dlp / youtube-transcript-api / openai /
  pinecone / fastapi dropped); `.env.example` refreshed with `GEMINI_API_KEY=` +
  `GEMINI_API_KEY_TTS=`; `tests/agents/voice/test_gemini_tts.py` (mocked);
  package `__init__.py`s; venv at `.venv/` (already gitignored).

## Files created / modified
Created:
- `agents/__init__.py`, `agents/shared/__init__.py`, `agents/voice/__init__.py`, `agents/m0/__init__.py`
- `agents/shared/logger.py`, `agents/shared/settings.py`, `agents/shared/exceptions.py`
- `agents/voice/models.py`, `agents/voice/gemini_tts.py`, `agents/voice/audio.py`
- `agents/m0/digests_input.py`, `agents/m0/render_audio.py`
- `requirements.txt`
- `tests/__init__.py`, `tests/agents/__init__.py`, `tests/agents/voice/__init__.py`, `tests/agents/voice/test_gemini_tts.py`
- `agents/m0/output/audio/digest-{1..5}.mp3` (deliverable; NOT gitignored)

Modified:
- `.env.example` (added Gemini section)

NOT touched: `.gitignore` (its `M` status predates this sub-phase — pre-existing
OpenMemory `CLAUDE.md` rule, unrelated). No TLDW donor files edited.

## Divergences from the plan (and why)
1. **`LLMClient` dependency replaced by an in-module `GeminiTTSClient`.** The
   donor `gemini_tts.py` calls `LLMClient.call_gemini_multispeaker_tts`, but
   `LLMClient` lives in `agents/pipeline/` (out of SP1's Files-touched) and pulls
   in openai/pinecone. I copied the exact proven Gemini call shape into a minimal
   `GeminiTTSClient` in `gemini_tts.py` so the chunking/voice-map logic stays
   verbatim and the spike's deps stay `google-genai`-only (Rule 2/3, surgical).
2. **`settings.py` / `exceptions.py` trimmed** to the M0 surface (Gemini keys;
   base+stage+TTS errors). Reintroduce the rest when those stages are ported.
3. **Output format = `.mp3`** (the donor's assembled-output format), not WAV.
4. **TTS pacing prompt changed** from "energetic, quick turn-taking" to a
   "measured broadcast-news cadence." First render came in too fast (3/5 under
   the 45s floor); the measured cadence lands near the locked ~55s and pushed
   4/5 over the floor on the deterministic lever (the read), without editing the
   scripts.
5. **Added `min_duration_ms` (default 46000) padding to `export_digest_audio`.**
   TTS pacing is non-deterministic; the tersest script (digest-2) still landed
   at 44.93s. The function now appends a trailing loop-point beat of silence when
   below the floor. This is deterministic and sits *after* the last segment, so
   the per-segment `segment_timings` SP2 uses are unaffected.

## Code-review findings + fixes
- **[medium, fixed]** Module-level `pytestmark = pytest.mark.asyncio` was applied
  to sync chunking tests → pytest warnings. Replaced with per-test
  `@pytest.mark.asyncio` on the 4 async tests.
- **[low, noted]** pydub emits a `DeprecationWarning: 'audioop'` on Python 3.13
  removal — third-party, not actionable in SP1.
- **Security:** key value never logged/printed. `SecretStr` throughout;
  `resolved_gemini_tts_key()` returns the key only to the genai client. grep
  confirmed no key-value logging; only env-var *names* appear in fix_suggestions.

## Validation results
- `ruff check agents/ tests/` → **All checks passed!**
- `ruff format --check` → **16 files already formatted** (pass)
- `pytest tests/agents/voice/test_gemini_tts.py` → **7 passed** (mocked, no real
  API). Asserts: (a) ALEX→Leda / JORDAN→Sadaltager at the call boundary +
  Person1/Person2 prompt mapping; (b) chunking on `<Person[12]>` under byte
  budget + no dropped oversized turn; (c) assembled turn order/indices preserved.
- `python -m agents.m0.render_audio` → wrote all 5 files (real Gemini calls,
  HTTP 200). **ffprobe durations (gate 45–70s):**
  - digest-1.mp3 — 50.61s — PASS
  - digest-2.mp3 — 46.00s — PASS
  - digest-3.mp3 — 55.89s — PASS
  - digest-4.mp3 — 47.49s — PASS
  - digest-5.mp3 — 47.37s — PASS
  All 5 have an mp3 audio stream.

## Definition of done: PASS
5 audio files written; each ffprobe 45–70s; the 3 mocked-test assertion groups
pass; Ruff check + format pass; no key value logged.

## Concerns for the orchestrator
1. **Audio format = `.mp3`** at `agents/m0/output/audio/digest-{1..5}.mp3`. SP4's
   Remotion manifest should point at `.mp3`.
2. **Per-turn duration data for SP2:** because Gemini renders each digest as ONE
   multi-speaker call, the output is a single audio segment per digest — so
   `assemble_episode` emits exactly ONE `SegmentTiming` per digest covering the
   whole speech span, NOT per-turn boundaries. **SP2 will only get each digest's
   total duration, not per-turn boundaries**, and must time-slice the transcript
   across the total duration itself (forced alignment, or proportional slicing by
   word count — the phase's Open-Q3 fallback). Total durations are the ffprobe
   values above; `digests_input.DIGESTS[i].turns` gives the exact ordered words.
   Note digest-2 has a trailing ~1.07s of padded silence after speech (it was
   44.93s pre-pad); SP2 should not place captions in the final ~1s of digest-2.
3. **Gemini free-tier quota wall (BLOCKER for re-renders today):** the key is on
   the free tier — `GenerateRequestsPerDayPerProjectPerModel-FreeTier` = **10
   TTS requests/day** for `gemini-2.5-flash-tts`. This run used all 10 (initial
   render + cadence re-render + a 3rd that 429'd). The digest-2 sub-46s file was
   brought to 46.00s by applying the *now-built-in* `export_digest_audio` padding
   out-of-band to the existing file (no new API call) — a fresh
   `python -m agents.m0.render_audio` reproduces the identical result once quota
   resets. **A full re-render of all 5 will 429 until the daily quota resets (~24h)
   or the project moves to a paid tier.** SP2/SP4 should reuse the existing
   on-disk audio, not re-render.
4. **Output MP3s are NOT gitignored** — the phase-end commit will include 5
   binary files (~1.0–1.3 MB each). Orchestrator decides whether to commit them.
5. `GEMINI_API_KEY_TTS` is empty in `.env`; the code correctly falls back to
   `GEMINI_API_KEY` (which is set and worked).
