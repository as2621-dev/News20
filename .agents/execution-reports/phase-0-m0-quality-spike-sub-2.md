# Phase 0 — Sub-phase 2 Execution Report

**Sub-phase:** Port forced alignment → word-by-word caption tracks
**Status:** SUCCESS
**Date:** 2026-05-29

## What was built

An **offline, transcript-time-slice forced aligner** that turns each digest's
known transcript (from `agents/m0/digests_input.py`) into a word-by-word caption
track timed against the real, ffprobe-measured audio duration — with exactly one
`#FACC15` highlight word per sentence. No audio was re-rendered; no external API
was called.

### Files touched (paths only)
- `agents/pipeline/__init__.py` (NEW — plumbing)
- `agents/pipeline/stages/__init__.py` (NEW — plumbing)
- `agents/pipeline/stages/forced_alignment.py` (NEW — the aligner; replaces the Whisper donor's contract)
- `agents/m0/align_captions.py` (NEW — driver, `python -m agents.m0.align_captions`)
- `tests/agents/pipeline/__init__.py` (NEW — plumbing)
- `tests/agents/pipeline/test_forced_alignment.py` (NEW — 16 tests)
- `agents/m0/output/captions/digest-{1..5}.captions.json` (NEW — 5 outputs)

I did **not** port the donor's `agents/pipeline/{models,json_utils}.py` — they
were not needed (the donor's word shape was an untyped dict; I defined clean
Pydantic v2 models instead). Surgical per Rule 3.

## Divergences (flagged per Rule 12)

1. **Whisper → transcript-time-slice (the big one).** The TLDW donor
   `forced_alignment.py` recovers per-word timing by calling the OpenAI Whisper
   API and reconciling with `difflib`. Per the user directive ("we already have
   the transcript; use the transcript to show the subtitle caption") this module
   does NOT call Whisper/OpenAI/any API. It heuristically time-slices the known
   transcript words proportional to a char-count spoken-length proxy across the
   speech span `[0, speech_end_s]`. Still "forced alignment" (known transcript →
   audio), just heuristic not acoustic — the phase's named Open-Q3 fallback. The
   deviation + rationale is documented at the top of the module. The single
   public entry point is `align_transcript_to_audio`.

2. **Cut→sentence highlight mapping rule.** The brief has 8 per-cut "caption
   keywords" but each digest splits into 10–11 sentences, so keywords can't map
   1:1 to sentences. Rule used: pass the 8 keywords as a flat POOL per digest;
   for each sentence, highlight the FIRST token matching ANY pooled keyword
   (normalized exact-or-substring match, so "data" matches "data-center"). A
   sentence with no pool hit falls back deterministically to its longest
   non-stopword content token (earliest on a tie). Result: every sentence gets
   exactly one highlight — never zero, never two. The keyword pools are
   transcribed by hand into `CAPTION_KEYWORD_POOLS` in the driver (markdown
   parsing is out of scope, same decision as `digests_input.py`).

3. **digest-2 trailing silence.** Honored SP1's measured ~1.07s trailing pad:
   `speech_end_s = 46.00 − 1.07 = 44.93s`, so no caption sits over silence.
   ffmpeg `silencedetect` could NOT isolate it acoustically at -30/-40/-50dB
   (the mp3 tail is continuous low-level codec noise), so I applied SP1's fact as
   a documented per-digest constant (`TRAILING_SILENCE_S`) rather than an
   unreliable runtime probe. All other digests use the full ffprobe duration
   (confirmed no meaningful trailing silence).

## Review findings + fixes (Steps B/C)

- **Dead code in a test** (`audio_path = ... if False else None` leftover) →
  removed. Fixed the resulting unused `Path` import too.
- **Per-sentence vs pool keyword contract** — initial draft indexed keywords
  1:1 to sentences (wrong, keywords < sentences). Refactored
  `_choose_highlight_index` to take the flat pool. (medium, fixed)
- No critical/high issues found.

## Validation (Step D)

- `ruff check agents/ tests/` → **All checks passed!**
- `ruff format --check agents/ tests/` → **22 files already formatted**
- `python -m agents.m0.align_captions` → wrote **5** caption JSONs.
- `pytest tests/agents/pipeline/test_forced_alignment.py -v` → **16 passed in 0.27s**
- Full suite `pytest tests/ -q` → **23 passed** (16 SP2 + 7 SP1; 1 pre-existing
  pydub `audioop` DeprecationWarning from SP1, unrelated).

The 4 DoD assertions are enforced by `_assert_track_invariants` and verified
BOTH on a synthetic fixture (unit) AND on every real digest + the 5 emitted
JSONs:
- (a) monotonic & non-overlapping word timings ✓
- (b) all timings within `[0, speech_end_s]`, `speech_end_s ≤ audio_duration_s` ✓
- (c) transcript word count == caption word count (130/130/134/129/121, verified
  paragraph-tokens == sentence-tokens == json-words for all 5) ✓
- (d) exactly one highlight per sentence (11/10/10/11/10) ✓

## Definition of done: PASS

5 caption JSONs produced; all 4 assertions hold on the real outputs; Ruff passes.

## Concerns for SP3/SP4

1. **Cosmetic quote artifact (low).** In digest-1 the sentence
   `But "close" isn't "done."` loses its final closing `"` on the last token
   (rendered token is `"done.`). Word count is preserved (no data loss);
   timing/highlight unaffected. Did not fight the splitter regex for this single
   quote-after-period edge case (Rule 2/3). SP3 may strip/normalize stray quotes
   at render if it matters visually.
2. **`forced_alignment.py` is 519 LoC** (guideline 500). ~56 of those are the
   one-stopword-per-line `frozenset` (Ruff formatting) plus heavy docstrings;
   real logic is < 200 lines. Did not split into a helper file — would be a
   premature abstraction that hurts readability (Rule 2). Flagging per Rule 12.
3. **Heuristic timing drift.** Proportional char-length slicing is an
   approximation, not acoustic truth — captions will drift somewhat from the
   actual spoken word, especially across numbers spoken as words ("81.6 billion"
   vs "eighty-one point six"). Acceptable for the M0 sound-off readability spike;
   if drift is visibly bad in SP4's MP4 smoke test, the syllable model could be
   refined or true acoustic alignment revisited post-M0.
4. **SP3 must match this exact JSON shape** (below) for its `CaptionTrack`
   component + `captionWordsAtFrame(track, frame)`.

## EXACT caption-track JSON shape SP3 must consume

```json
{
  "digest_id": "digest-1",
  "audio_duration_s": 50.611,
  "speech_end_s": 50.611,
  "sentence_count": 11,
  "words": [
    { "word": "The",    "start_s": 0.0,  "end_s": 0.211, "sentence_index": 0, "is_highlight": false },
    { "word": "target", "start_s": 1.83, "end_s": 2.14,  "sentence_index": 0, "is_highlight": true  }
  ]
}
```

Field contract:
- `digest_id` (str) — "digest-1" .. "digest-5", also the filename stem.
- `audio_duration_s` (float, > 0) — true ffprobe duration of the mp3.
- `speech_end_s` (float, in `(0, audio_duration_s]`) — captions never extend
  past this; equals `audio_duration_s` except digest-2 (44.93 vs 46.0).
- `sentence_count` (int ≥ 0).
- `words` (ordered list), each:
  - `word` (str, verbatim incl. casing + attached punctuation)
  - `start_s` (float ≥ 0, inclusive)
  - `end_s` (float ≥ start_s, exclusive; contiguous: `words[i].end_s == words[i+1].start_s`)
  - `sentence_index` (int ≥ 0)
  - `is_highlight` (bool) — exactly one `true` per `sentence_index`.
