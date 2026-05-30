# Phase 0 / Sub-phase 3 — Remotion 9:16 digest compositor

**Status:** SUCCESS
**Date:** 2026-05-29

---

## REWORK 2026-05-29 — pivot to single-poster format (poster-pipeline.md §2/§10)

The product pivoted from the 8-cut Ken Burns format to ONE poster-grade image per story.
`reference/poster-pipeline.md` is now the single source of truth.

**What changed (all within `remotion/`):**
- `manifest.ts` — dropped `Cut`/`cuts`; `DigestManifest` is now single-poster: `posterSrc`
  (one 9:16 image), `durationInFrames` (SP4 sets `= round(audio_duration_s * fps)`), optional
  `kenBurns`. `CaptionWord`/`CaptionTrack`/`KenBurns` types unchanged; `captionTrack` still
  matches SP2's `digest-{n}.captions.json` verbatim.
- `Digest.tsx` — renders one full-frame poster (via `KenBurnsImage` fed `posterSrc`) for the
  whole timeline; the `HeadlineCard` now shows only as a brief INTRO overlay (first 75 frames
  ≈2.5s) then cross-fades out (last 15 frames); removed the 8-cut `<Sequence>` tiling. Static-
  first default Ken Burns (`scale 1.0→1.04`, NO pan) applied when `kenBurns` is absent — gentle
  enough that motion never sits distractingly under the still caption band (§10 failure mode).
- `Root.tsx` — `calculateMetadata` now derives `durationInFrames` from `props.durationInFrames`
  (was sum-of-cuts), `fps` from `props.fps`; static fixture values remain the no-props fallback.
- `fixtures/sample-manifest.json` — single-poster shape; reuses `public/fixtures/assets/cut-1.png`
  as `posterSrc`; `durationInFrames: 1518` (50.60s @ 30fps). Caption track preserved verbatim.
- `captionWordsAtFrame.ts` + test — NO change (format-agnostic); 6/6 still green.

**Verification (no commit):**
- `npx tsc --noEmit` — clean (strict, exit 0).
- `npx vitest run` — 6/6 pass, no regression.
- `npx remotion compositions` — `Digest  30  1080x1920  1518 (50.60 sec)` (derived from props).
- Smoke stills Read:
  - `out/poster-early.png` (frame 30, t=1.0s): poster fills 1080×1920 on near-black `#020617`;
    headline intro card visible (mono label + Inter-500 headline + yellow accent bar); caption
    "The U.S." appearing.
  - `out/poster-mid.png` (frame 300, t=10.0s): the SAME poster fills the frame, scaled slightly
    larger than frame 30 (gentle drift confirmed); headline intro fully faded; caption
    "Officials called it a narcotics strike." legible in the lower third, exactly one word
    ("narcotics") in yellow `#FACC15`.

**FINAL single-poster `DigestManifest` (SP4 populates this):**
```ts
export interface CaptionWord { word: string; start_s: number; end_s: number; sentence_index: number; is_highlight: boolean; }
export interface CaptionTrack { digest_id: string; audio_duration_s: number; speech_end_s: number; sentence_count: number; words: CaptionWord[]; }
export interface KenBurns { startScale: number; endScale: number; startTranslateX: number; endTranslateX: number; startTranslateY: number; endTranslateY: number; }
export type DigestManifest = {
  digest_id: string;
  audioSrc: string;            // .mp3 (SP1)
  posterSrc: string;           // single 9:16 poster; SP4 -> staged assets/m0/digest-<n>/poster.png copied into remotion/public
  headlineText: string;        // brief intro card
  durationInFrames: number;    // SP4: round(audio_duration_s * fps)
  fps: number;                 // 30
  width: 1080;
  height: 1920;
  kenBurns?: KenBurns;         // optional; omit for the gentle static-first default
  captionTrack: CaptionTrack;  // SP2 shape, verbatim
};
```
> Note: the 7 unused `cut-2..8.png` fixture placeholders remain on disk (harmless, tiny); only
> `cut-1.png` is now referenced as the poster.

**The 8-cut writeup below is the SUPERSEDED original SP3 record (kept for history).**

---

## What was built
A standalone Remotion subproject (`remotion/`) with a `Digest` composition at 1080×1920 / 30fps / 1650 frames (55.00s) that renders from a single render manifest:
- **8 image cuts** sequenced back-to-back with Ken Burns motion (`KenBurnsImage`); durations tile the audio.
- **Headline card overlay on cut 1 only** (`HeadlineCard`) — the 0–2s hook, with a near-black scrim, Inter-500 headline, JetBrains-Mono-600 metadata label, and a `#FACC15` accent bar.
- **Word-by-word caption track** (`CaptionTrack`) — bold white, black outline, lower-middle third, one `#FACC15` highlight word per sentence; driven by the pure `captionWordsAtFrame` mapping.
- **Audio** from the manifest (`<Audio>`).
- A self-contained fixture (`sample-manifest.json` + 8 tiny gradient PNGs) so it renders with zero dependency on SP1/SP2/SP4.

## Files touched (all under `remotion/`)
- `package.json`, `tsconfig.json`, `remotion.config.ts`, `vitest.config.ts`, `.gitignore`
- `src/index.ts` (entry), `src/Root.tsx` (registers `Digest`), `src/Digest.tsx` (composition)
- `src/components/KenBurnsImage.tsx`, `src/components/CaptionTrack.tsx`, `src/components/HeadlineCard.tsx`
- `src/manifest.ts` (the render-manifest contract — single source of truth for SP4)
- `src/captionWordsAtFrame.ts` (pure fn) + `src/captionWordsAtFrame.test.ts` (6 tests)
- `src/fixtures/sample-manifest.json` (fixture)
- `public/fixtures/assets/cut-{1..8}.png` (1.6–2.9 KB placeholder stills; `staticFile` resolves from `public/`)
- `out/smoke-headline.png`, `out/smoke-caption.png` (gitignored smoke stills)

## Divergences from the brief
- **Placeholder assets live in `public/fixtures/assets/`, not `src/fixtures/assets/`.** Remotion's `staticFile()` resolves only from `public/`. The fixture JSON stays at `src/fixtures/sample-manifest.json` as specified (imported by `Root.tsx` for default props). The brief's `src/fixtures/assets/` path was an "e.g." suggestion; this is the path that actually renders.
- `DigestManifest` is declared as a `type` (not `interface`) so it satisfies Remotion's `<Composition>` constraint `Props extends Record<string, unknown>` (interfaces lack the implicit index signature). All nested shapes remain interfaces.
- Fonts are referenced by `font-family` with system fallbacks (Inter / JetBrains Mono named first) rather than webfont-fetched, to keep the still/preview render dependency-free. Noted in `remotion.config.ts` that SP4 can add `@remotion/google-fonts` if exact webfonts are required for the final MP4s.

## Review findings + fixes
- **Fixed (during build):** `JSX.Element` is unavailable under React 19's new JSX transform → switched to `ReactElement` / typed `Digest` as `FC<DigestManifest>`. Typecheck then clean.
- **Noted, not fixed (low/cosmetic):** at the very start of cut 1 the first caption word overlaps the centered headline. Transient, only during the 0–2s hook, no contract impact. SP4/M1 polish could suppress captions in the headline window. Flagged per Rule 12.
- **Confirmed against real SP2 output:** `agents/m0/output/captions/digest-1.captions.json` already exists and its shape matches `manifest.ts`'s `CaptionTrack` exactly — SP2's JSON drops straight into `captionTrack`.

## Validation
- **Typecheck:** `npx tsc --noEmit` — PASS (strict, `noUncheckedIndexedAccess`, no output, exit 0).
- **Unit test:** `npx vitest run` — PASS, 6/6 in `captionWordsAtFrame.test.ts`. Asserts: active word inside a known interval (and NOT its neighbours), current-sentence reveal scope (sentence 1 never leaks into sentence 0), exactly one highlight in the visible set, half-open boundary behaviour at exactly 1.0s (→ "moved.", never "target"), clamp past `speech_end_s` (no words), and first-word activation at frame 0.
- **Composition registration:** `npx remotion compositions` lists `Digest  30  1080x1920  1650 (55.00 sec)`.
- **Smoke stills (rendered + Read):**
  - `out/smoke-headline.png` (frame 15): 1080×1920, near-black `#020617` canvas with cut-1 blue gradient behind a scrim; "NEWS20 / DAILY BRIEFING" mono label; white Inter headline "U.S. strikes another boat in the Caribbean" with a yellow `#FACC15` left accent bar; first caption word "The" appearing. Headline card clearly present.
  - `out/smoke-caption.png` (frame 300, t=10.0s, cut 3): 1080×1920, green-gradient cut-3 still scaled by Ken Burns; caption "Officials called it a narcotics strike." in bold white with black outline in the lower-middle third; exactly one word — "narcotics" — in yellow `#FACC15`. Confirms still + word-by-word captions + single highlight.

## Definition of done: PASS
- Composes from `fixtures/sample-manifest.json` at 1080×1920, 55s timeline — verified (composition list + both stills).
- Captions reveal word-by-word with highlight in yellow — verified (caption still: one yellow word in a revealed sentence).
- Headline card on cut 1 — verified (headline still).
- Ken Burns visible — verified (cut-3 still shows the still scaled/panned; per-cut presets in the fixture).
- `captionWordsAtFrame` unit test passes (6/6).
- `manifest.ts` is the single source of truth for SP4.

## Ken Burns / caption-style decisions
- **Ken Burns:** linear interpolation of `scale` + `translateX/Y` from start→end over each cut's frames, clamped at both ends. Fixture alternates gentle zoom-in / zoom-out + pan per cut so motion is always visible. SP4 supplies per-cut params via the `kenBurns` field.
- **Captions:** half-open `[start_s, end_s)` interval selection (so contiguous words never both match a boundary frame); the whole current sentence is revealed up to the active word (word-by-word build-up), then cleared at sentence change; black `WebkitTextStroke` + `paintOrder: stroke fill` + text-shadow for sound-off legibility on any still; the active word gets a subtle 2px lift.

## FINAL `manifest.ts` contract for SP4 to populate
```ts
export interface CaptionWord {
  word: string;            // verbatim token (keep casing + attached punctuation)
  start_s: number;
  end_s: number;           // === next word's start_s (contiguous)
  sentence_index: number;
  is_highlight: boolean;   // exactly one true per sentence_index
}

export interface CaptionTrack {
  digest_id: string;
  audio_duration_s: number;
  speech_end_s: number;    // captions never extend past this
  sentence_count: number;
  words: CaptionWord[];    // flat, monotonic, contiguous, within [0, speech_end_s]
}

export interface KenBurns {
  startScale: number; endScale: number;
  startTranslateX: number; endTranslateX: number;
  startTranslateY: number; endTranslateY: number;
}

export interface Cut {
  imageSrc: string;        // staticFile-relative path, OR absolute path / http(s) URL
  durationInFrames: number;
  kenBurns: KenBurns;
}

export type DigestManifest = {
  digest_id: string;
  audioSrc: string;        // .mp3 path (SP1 output)
  headlineText: string;    // cut-1 card
  cuts: Cut[];             // exactly 8; cut 1 carries the headline overlay (no 9th cut)
  captionTrack: CaptionTrack;
  fps: number;             // 30
  width: 1080;
  height: 1920;
};
```

### SP4 wiring notes
- `imageSrc`/`audioSrc`: relative paths resolve via `staticFile()` from `remotion/public/`; absolute paths and `http(s)` URLs pass through untouched (`resolveSrc` in `Digest.tsx`). For SP4's real files, emit absolute paths or stage assets under `public/`.
- `durationInFrames` across the 8 cuts must sum to the timeline length; the composition's total duration in `Root.tsx` is the sum of cut durations. SP4 must size cuts to tile the real audio (~50–55s @ 30fps).
- `captionTrack` is SP2's `digest-{n}.captions.json` verbatim — confirmed matching the real output already on disk.

## Concerns
1. **Composition `durationInFrames` = sum of cut frames, not audio length.** If SP4's summed cut frames don't match the real audio duration, the video will be shorter/longer than the audio. SP4's `build_render_manifest` must tile cuts to the `ffprobe` audio duration (phase DoD requires 45–70s with an audio stream).
2. **Headline/caption overlap during the hook** (cosmetic, flagged above) — candidate for SP4/M1 polish.
3. **Fonts are system-fallback** for the spike; if the audience-test stills must show exact Inter/JetBrains Mono, SP4 should add `@remotion/google-fonts`.
4. `npm install` reported npm-audit advisories (transitive, in the Remotion toolchain) — not addressed; out of scope for the spike.
