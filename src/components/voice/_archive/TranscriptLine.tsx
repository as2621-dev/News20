"use client";

/**
 * TranscriptLine — one line of live voice transcription (Phase 3 SP4).
 *
 * Ports the prototype's `#v-transcript` paragraph (port-map §5; prototype
 * `voiceConversation`): the single centred line under the orb that shows either
 * what the user just said (input transcription) or what blip is answering (output
 * transcription). The prototype distinguished them purely by opacity — the user's
 * question rendered `text-white`, the grounded answer `text-white/85`, and the
 * idle prompt dim. That contract is preserved here, keyed by
 * {@link TranscriptLineProps.transcript_role}.
 *
 * Maps 1:1 to the Gemini Live transcription frames phase-3b streams in
 * (`serverContent.inputTranscription.text` → `"input"`,
 * `outputTranscription.text` → `"output"`); SP3's hook surfaces those, this
 * component only renders them. Presentational — text in, nothing out.
 *
 * @example
 * <TranscriptLine transcript_role="input" transcript_text="What led to this?" />
 *
 * @example
 * <TranscriptLine transcript_role="output" transcript_text="Three outlets report…" />
 */

/** Which side of the conversation a transcript line belongs to. */
export type TranscriptRole = "input" | "output";

export interface TranscriptLineProps {
  /**
   * Whether this line is the user's speech (`"input"`) or blip's answer
   * (`"output"`). Drives the prototype opacity contract: input is full-white,
   * output is `white/85`.
   */
  transcript_role: TranscriptRole;
  /**
   * The transcribed text to render. An empty string renders nothing (no empty
   * paragraph shell) so a not-yet-spoken turn shows blank, not a stray box.
   */
  transcript_text: string;
  /**
   * Whether the line is still streaming (Gemini Live emits transcription in
   * fragments). When true the line gets `aria-busy` so assistive tech announces it
   * as in-progress rather than final. Defaults to false.
   */
  is_streaming?: boolean;
}

/**
 * Render one transcription line.
 *
 * Input lines render full-white (the user's question stands out, prototype
 * `<span class="text-white">`); output lines render at `white/85` (the grounded
 * answer, prototype `<span class="text-white/85">`). An empty `transcript_text`
 * renders `null` — never an empty paragraph.
 */
export function TranscriptLine({ transcript_role, transcript_text, is_streaming = false }: TranscriptLineProps) {
  if (transcript_text.length === 0) {
    // Reason: a turn with no text yet must render nothing, not an empty shell —
    // mirrors the detail components' null-branch discipline (OpposingViewCard).
    return null;
  }

  const roleColorClass = transcript_role === "input" ? "text-white" : "text-white/85";

  return (
    <p
      data-transcript-line={transcript_role}
      aria-live="polite"
      aria-busy={is_streaming}
      className={`mx-auto w-full max-w-[310px] text-center font-sans text-[15px] leading-relaxed ${roleColorClass}`}
    >
      {transcript_text}
    </p>
  );
}
