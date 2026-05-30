/**
 * Render-manifest contract — the single source of truth for the Python -> Remotion seam.
 *
 * Single-poster format (per `reference/poster-pipeline.md` §2): ONE poster-grade 9:16 image
 * is the background for the whole timeline; headline + captions are separate overlay layers.
 * This supersedes the earlier 8-cut Ken Burns format.
 *
 * Sub-phase 4 (`agents/m0/build_render_manifest.py`) assembles one `DigestManifest` per digest
 * and passes it to the `Digest` composition via Remotion `--props`. Sub-phase 2's caption JSON
 * (`agents/m0/output/captions/digest-{1..5}.captions.json`) drops straight into `captionTrack`
 * because `CaptionTrack` below mirrors that emitted shape verbatim.
 *
 * Invariants SP4 must honour (relied on by `captionWordsAtFrame` + the components):
 * - `posterSrc` is a single 9:16 poster image; the headline card is a brief INTRO overlay on it.
 * - `durationInFrames` = `round(audio_duration_s * fps)` — the poster holds for the full audio.
 * - `captionTrack.words` are contiguous (`words[i].end_s === words[i+1].start_s`), monotonic,
 *   and all within `[0, speech_end_s]`; exactly one `is_highlight: true` per `sentence_index`.
 * - `word` is verbatim from the script (original casing + attached punctuation preserved).
 */

/** A single forced-alignment word, exactly as SP2 emits it. */
export interface CaptionWord {
  /** Verbatim token from the script — keep casing and attached punctuation. */
  word: string;
  /** Word start time in seconds from the top of the audio. */
  start_s: number;
  /** Word end time in seconds; equals the next word's `start_s` (contiguous). */
  end_s: number;
  /** Zero-based sentence this word belongs to. */
  sentence_index: number;
  /** True for the single highlight keyword in this sentence (rendered in `#FACC15`). */
  is_highlight: boolean;
}

/** Per-digest caption track — mirrors SP2's `digest-{n}.captions.json` shape verbatim. */
export interface CaptionTrack {
  /** Digest identifier, e.g. `"digest-1"`. */
  digest_id: string;
  /** Total audio duration in seconds (from `ffprobe`). */
  audio_duration_s: number;
  /** Timestamp where speech ends; captions never extend past this. */
  speech_end_s: number;
  /** Number of sentences in the script (one highlight per sentence). */
  sentence_count: number;
  /** Flat, monotonic, contiguous list of timed words. */
  words: CaptionWord[];
}

/**
 * Ken Burns motion parameters — linear interpolation start -> end across the full timeline.
 *
 * Static-first (poster-pipeline §10): the default is a VERY gentle slow zoom with NO pan so a
 * long-held still does not feel dead, while the caption band (a separate overlay) stays put.
 * Motion under text is the explicit failure mode, so keep this imperceptible.
 */
export interface KenBurns {
  /** Scale at the start of the timeline (1 = no zoom). */
  startScale: number;
  /** Scale at the end of the timeline. */
  endScale: number;
  /** Horizontal pan offset in px at the start (keep 0 for static-first). */
  startTranslateX: number;
  /** Horizontal pan offset in px at the end (keep 0 for static-first). */
  endTranslateX: number;
  /** Vertical pan offset in px at the start (keep 0 for static-first). */
  startTranslateY: number;
  /** Vertical pan offset in px at the end (keep 0 for static-first). */
  endTranslateY: number;
}

/**
 * The full render manifest the `Digest` composition consumes as input props.
 *
 * Declared as a `type` (not `interface`) so it satisfies Remotion's
 * `Props extends Record<string, unknown>` constraint on `<Composition>`.
 */
export type DigestManifest = {
  /** Digest identifier, e.g. `"digest-1"`. */
  digest_id: string;
  /** Audio source — an `.mp3` path (SP1 outputs mp3). */
  audioSrc: string;
  /** The single poster image (9:16), full-frame background for the whole timeline. */
  posterSrc: string;
  /** Headline text rendered on the brief intro overlay card. */
  headlineText: string;
  /** Timeline length in frames; SP4 sets this to `round(audio_duration_s * fps)`. */
  durationInFrames: number;
  /** Frames per second (locked at 30). */
  fps: number;
  /** Composition width in px (locked at 1080). */
  width: 1080;
  /** Composition height in px (locked at 1920). */
  height: 1920;
  /** Optional Ken Burns drift; if absent, a very gentle default zoom is applied. */
  kenBurns?: KenBurns;
  /** Word-by-word caption track (SP2 shape — unchanged). */
  captionTrack: CaptionTrack;
};
