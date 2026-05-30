# Phase 1 — Sub-phase 2 execution report: Typed feed contract + M0 fixtures + karaoke selector

**Status:** SUCCESS
**Sub-phase:** 2 of 4 (`phase-1-audio-first-reel`)
**Scope:** the cross-phase data contract, the M0→canonical normalizer, the pure karaoke selector, the fixture feed provider, the 5 M0 fixtures, and their unit tests. No UI (SP3), no commit (orchestrator owns that).

---

## What was built

A typed, backend-free feed layer + the pure karaoke selection logic the reel UI (SP3) renders from, plus the 5 real M0 digests bundled as fixtures.

### Files created
- `src/types/feed.ts` — the **cross-phase contract**. `WordToken`, `CaptionSentence`, `Digest`, `Story`, `AnchorSpeaker` (`"ALEX" | "JORDAN"`), `SegmentKey`. Field names aligned verbatim to `reference/supabase-schema.md` so Phase 3 swaps only the feed-provider impl. JSDoc documents the column→field provenance.
- `src/lib/feed/normalizeM0Captions.ts` — pure. M0 on-disk shape (`M0CaptionTrack`/`M0CaptionWord`, times in **seconds**) → `CaptionSentence[]`. Groups flat `words[]` by `sentence_index`, seconds→ms via `Math.round(s*1000)`, preserves the verbatim word sequence (count + order), derives `sentence_text` / `highlight_keyword` / window / alternating `anchor_speaker`. Logs a warning (does not throw) if a sentence ≠ exactly one highlight.
- `src/lib/captions/captionState.ts` — pure `captionStateAtTime(captionSentences, currentTimeMs, speechEndMs)`. The karaoke selector. (Design choice below.)
- `src/lib/feed/fixtureFeed.ts` — `export async function getFeed(): Promise<Story[]>` returning the 5 stories. `async` on purpose (Phase 3 swaps body for Supabase). Bundles the 5 M0 caption JSONs via build-time `import` + inline story metadata transcribed from `data.js`; audio/poster are `/fixtures/...` URL refs.
- `src/lib/logger.ts` — **new shared util** (see "Exception" below). Minimal structured-JSON console logger (CLAUDE.md §5), TS analogue of the agents' `structlog`. `info`/`warn`/`error`.
- `tests/lib/normalizeM0.test.ts` — 7 tests vs real `digest-1`/`digest-3` JSON.
- `tests/lib/captionState.test.ts` — 14 tests vs real normalized `digest-1`/`digest-2` tracks, organized by invariant (a)–(e).
- `public/fixtures/audio/digest-{1..5}.mp3`, `public/fixtures/captions/digest-{1..5}.captions.json`, `public/fixtures/posters/digest-{1..5}.png` — copied (real bytes, `cp`) from `agents/m0/output/**` + `assets/m0/digest-N/poster.png`.

### Files modified
None of SP1's. Only ADDED under `src/types`, `src/lib`, `public/fixtures`, `tests/lib` (as permitted). `tests/lib/tokens.test.ts` (SP1) untouched and still green.

---

## The captionState design choice (Rule 1 — resolved deliberately)

**Decision: two orthogonal axes per word, not one 4-value enum.** Each rendered word returns `{ word_text, timing: 'dim'|'spoken'|'active', is_highlight: boolean, css_class_names }`.

**Why.** The phase file proposed a single per-word state `'dim'|'spoken'|'active'|'highlight'`, but the ported CSS in `src/app/globals.css` proves highlight is **not** mutually exclusive with the timing states: `.caption .w.hl` is `#FACC15` regardless of spoken/active, and `.caption .w.hl.active` coexists (the current word stays `.active` even when it is also the one keyword). A single enum would force a choice between "active" and "highlight" for the current keyword and lose one of the two true facts. Splitting into `timing` (the karaoke progress) + an independent `is_highlight` keeps **both** port-map §3.1 facts true and is fully testable. `css_class_names` is a convenience string (`"w"` + `spoken`/`active` + `hl`) so SP3 can spread it directly and stay byte-compatible with the prototype markup; `.w.hl.active` falls out naturally (verified: `target` at 2200ms → `"w active hl"`).

The selector also returns `current_sentence_index`, `current_sentence`, and `current_speaker` so SP3 renders the speaker label without re-deriving.

**Invariants enforced + tested:** (a) word whose `[start_ms,end_ms)` contains `t` is `active`; (b) earlier words `spoken`, later `dim`, inter-word gaps resolve to `spoken` (never a phantom active); (c) **nothing active at `t >= speech_end_ms`** (covers digest-2's trailing ambience); (d) exactly one highlight per current sentence; (e) current sentence = the `[sentence_start_ms,sentence_end_ms)` owner, with the last sentence staying "sticky" through the post-speech tail so the label persists.

---

## M0 approximation flags (Rule 12 — surfaced, not hidden)

1. **Per-sentence speaker is an alternation approximation.** The M0 caption JSON has NO speaker field, and M0 sentences ≠ TTS script turns, so the true per-sentence speaker is lost. `anchor_speaker = anchors[sentence_index % 2]`, consistent with the phase's "time-sliced is good enough" thesis (Open Q1). ALEX/JORDAN only. This is the documented M0 limitation, not a bug; if speaker accuracy ever matters, the digest generator must emit a per-sentence speaker.
2. **M0 sentence structure differs from the prototype `data.js` captions.** The prototype `captions[]` arrays are mock 7–8-sentence tracks; the real M0 JSON has 10–11 sentences with different wording. The fixture provider therefore uses M0 JSON as the karaoke source and pulls ONLY headline/segment/anchors from `data.js`. (Not a divergence — the brief mandates this.)

---

## Divergences / corrections to the brief

- **Posters are real PNGs, not JPEG-bytes-as-`.png`.** The phase file + my brief warned the `assets/m0/digest-N/poster.png` files are JPEG bytes despite the `.png` name. `file(1)` on the copied bytes reports `PNG image data, 1080 x 1920, 8-bit/color RGB` for all 5. So they are genuine PNGs — **the JPEG-as-`.png` concern does NOT apply** and SP3 has nothing to work around. (Note also the dimensions are 1080×1920, not the 768×1376 the phase file mentions — but for an ambient `<img>` wash that's irrelevant.) Kept the `.png` filenames and the `/fixtures/posters/digest-N.png` URL scheme as planned.

---

## Step B/C review findings + fixes

Self-reviewed the diff for logic, type-safety, purity, CLAUDE.md adherence; severity-tagged:
- **[low] No shared `logger` existed (SP1 didn't create `src/lib/logger`).** `normalizeM0Captions`/`fixtureFeed` need structured logging per CLAUDE.md §5. **Fix:** created the minimal `src/lib/logger.ts` (see Exception). Not masking — it's a required shared util.
- **[low] JSON-import typing.** Bundled M0 JSONs import as inferred object types; cast `as M0CaptionTrack` at the single import boundary (documented inline) rather than sprinkling `any`. `grep` confirms **zero** `any` / `@ts-ignore` / `eslint-disable` in the new source.
- **[low] Purity.** `normalizeM0Captions` and `captionStateAtTime` are pure (no I/O, no mutation of inputs; the internal grouping `Map` is local). `getFeed` only reads bundled constants. Confirmed.
- No critical/high issues found.

Biome `--write` made 2 cosmetic fixes (import ordering in `fixtureFeed.ts`; one long-line wrap in `normalizeM0.test.ts`); re-verify clean. Re-ran tsc + vitest after those edits — still green.

---

## Step D validation (run myself)

| Gate | Command | Result |
|---|---|---|
| Tests | `npx vitest run` | **PASS** — `Test Files 3 passed (3)`, `Tests 29 passed (29)` |
| Vitest scope | `--reporter=verbose` | only `tests/lib/**` (tokens + normalizeM0 + captionState) — never touches the Python `tests/agents/**` |
| Types | `npx tsc --noEmit` | **PASS** — exit 0 |
| Lint | `npm run lint` (Biome) | **PASS** — `Checked 19 files`, `No fixes applied` |
| Build | `npm run build` | **PASS** — exit 0, `✓ Exporting (2/2)`, emits `out/` |
| Fixtures | `ls -la` | all 15 present + non-zero in `public/fixtures/**` AND copied into `out/fixtures/**`; zero zero-byte files |

Build note: a pre-existing `MODULE_TYPELESS_PACKAGE_JSON` warning fires on SP1's `tailwind.config.ts` (no `"type":"module"` in `package.json`). It is a warning, not an error, originates from SP1's files, and is out of my surgical scope — flagging only.

No fix attempts were needed beyond the Biome auto-format (gates passed first try).

---

## Step E — Definition of Done (per phase file SP2)

| DoD item | Verdict | Evidence |
|---|---|---|
| `captionStateAtTime` returns correct spoken/active/highlight at given timestamps vs a real M0 track | **PASS** | invariant (a)/(b) tests at hand-verified ms (`target` active at 2200ms; `The` spoken / `inside` dim) |
| Exactly one highlight per sentence | **PASS** | invariant (d) sweep over the whole track |
| No word `active` past `speech_end` | **PASS** | invariant (c) — incl. digest-2's `speech_end 44930ms < duration 46000ms` tail swept at 100ms steps |
| `normalizeM0Captions` preserves verbatim word sequence flat→sentences | **PASS** | flattened tokens `.toEqual(raw words[])`, length 130 |
| seconds→ms correct | **PASS** | `[0,9463]ms` sentence window, `target [2007,2509]ms` |
| `fixtureFeed` returns 5 typed stories | **PASS** | `getFeed()` DoD smoke (temp test, since removed): 5 stories, correct ids/URLs/segments/accents, non-empty caption tracks |
| Tests fail on wrong selection logic, not merely on compile (Rule 9) | **PASS** | all assertions are value assertions vs real-artifact ground truth |

---

## Exception (per brief — note JSON-import / setup shims)

- **Added `src/lib/logger.ts`** — not in the SP2 file list, but CLAUDE.md §5 mandates structured logging and SP1 created no shared logger. It's a tiny, dependency-free shared util under my allowed `src/lib` scope. If the orchestrator prefers it elsewhere or consolidated later, it's a single file with one named export `logger`.
- **No tests/setup file or JSON `.d.ts` shim was needed** — `tsconfig.json` already has `resolveJsonModule: true` and Vite/Vitest handle JSON imports natively. The M0 JSONs import + type-check cleanly via an `as M0CaptionTrack` cast at the import boundary (tsc exit 0 confirms).
- **No fetch mock needed** in tests — fixtures are build-time-bundled imports, exactly as the brief requested.

---

## Concerns SP3/SP4 MUST know (the contract SP3 renders from)

1. **Import surface.**
   - `import { getFeed } from "@/lib/feed/fixtureFeed";` → `Promise<Story[]>` (5 stories, order `digest-1..5`).
   - `import { captionStateAtTime } from "@/lib/captions/captionState";`
   - Types: `import type { Story, CaptionSentence, WordToken, AnchorSpeaker, SegmentKey } from "@/types/feed";`
   - `import { logger } from "@/lib/logger";`

2. **`Story` shape (what the reel renders per story):** `digest_id`, `headline`, `segment_key`, `segment_label`, `segment_accent_hex` (set `--accent` from this), `anchors: [AnchorSpeaker, AnchorSpeaker]`, `digest_audio_url` (`/fixtures/audio/digest-N.mp3` → `<audio src>`), `audio_duration_ms` (progress-bar denominator), `speech_end_ms` (**pass THIS to the selector**, not duration — they differ for digest-2), `poster_url` (`/fixtures/posters/digest-N.png` → ambient `<img>`), `caption_sentences: CaptionSentence[]`.

3. **Driving the karaoke (the exact call):**
   `const state = captionStateAtTime(story.caption_sentences, audioRef.current.currentTime * 1000, story.speech_end_ms);`
   Sample `currentTime` each `requestAnimationFrame` (sample, don't accumulate — port-map §3.1).
   Returns:
   - `state.current_sentence_index` (`-1` before the first sentence) — render only the current sentence's words.
   - `state.current_speaker` (`AnchorSpeaker | null`) — **the speaker label comes straight from here**, no re-derivation. Map to fixed identity colours ALEX `#6C8CFF` / JORDAN `#C792EA` (per phase file / `supabase-schema.md`).
   - `state.current_sentence` (`CaptionSentence | null`).
   - `state.words: { word_text, timing: 'dim'|'spoken'|'active', is_highlight, css_class_names }[]`.

4. **Rendering a word (byte-compatible with `globals.css`):** each word span gets `className = word.css_class_names` (already `"w"` + `spoken`/`active` + `hl`). The CSS classes `.caption .w` / `.spoken` / `.active` / `.hl` / `.hl.active` already exist from SP1 — do NOT restyle. A word can be `active` AND `is_highlight` (current keyword) → `"w active hl"`; that's intentional.

5. **Speaker label persistence:** in digest-2's trailing tail (audio runs past `speech_end_ms`), the selector keeps the last sentence current and all words `spoken`, so the speaker label does not blank out. SP4's auto-advance should still fire on the audio `ended` event (port-map §3.1), which is `audio_duration_ms`-driven, NOT `speech_end_ms`.

6. **Fixtures:** 5 mp3s + 5 caption JSONs + 5 posters live in `public/fixtures/{audio,captions,posters}/digest-{1..5}.{mp3,captions.json,png}`; the static export copies them to `out/fixtures/**`. **Posters are genuine PNGs** (1080×1920) — the JPEG-as-`.png` warning in the phase file is inaccurate for these files; no workaround needed. Audio is real TTS (~1.1–1.3 MB each).

7. **`speech_end_ms` vs `audio_duration_ms`:** equal for digests 1/3/4/5; for digest-2, `speech_end_ms = 44930` and `audio_duration_ms = 46000` (1.07s ambient tail). Anywhere SP3/SP4 reasons about "narration finished" vs "audio finished," use the right one.

> **Next:** SP3 — build the reel surface (`Reel.tsx`/`ReelStory.tsx`/`ReelChrome.tsx`/`KaraokeCaption.tsx` + `useReelAudio.ts` + `gestures.ts`) consuming `getFeed()` and `captionStateAtTime(...)` exactly as in concern #3, rendering words via `css_class_names`.
