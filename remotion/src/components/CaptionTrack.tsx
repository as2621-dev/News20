import type { ReactElement } from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import { captionWordsAtFrame } from '../captionWordsAtFrame';
import type { CaptionTrack as CaptionTrackData } from '../manifest';

const CAPTION_HIGHLIGHT = '#FACC15';
const CAPTION_WHITE = '#FFFFFF';

/** Props for the overlaid word-by-word caption track. */
export interface CaptionTrackProps {
  /** The digest caption track (SP2 shape). */
  track: CaptionTrackData;
}

/**
 * Sound-off caption overlay: bold white text with a black outline in the lower-middle third,
 * revealed word-by-word as the audio plays. Exactly one keyword per sentence renders in
 * `#FACC15`. Timing is computed by the pure `captionWordsAtFrame` mapping, so the visual
 * stays in lockstep with the unit-tested logic.
 */
export function CaptionTrack({ track }: CaptionTrackProps): ReactElement | null {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const { visibleWords, activeWord } = captionWordsAtFrame(track, frame, fps);
  if (visibleWords.length === 0) {
    return null;
  }

  return (
    <AbsoluteFill
      style={{
        // Lower-middle third: anchor the caption block at ~62% of the height.
        justifyContent: 'flex-start',
        alignItems: 'center',
        paddingTop: '62%',
        paddingLeft: 64,
        paddingRight: 64,
      }}
    >
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          gap: '0 16px',
          maxWidth: 900,
          fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
          fontWeight: 800,
          fontSize: 64,
          lineHeight: 1.1,
          textAlign: 'center',
        }}
      >
        {visibleWords.map((word, index) => {
          const isActive = activeWord !== null && word.start_s === activeWord.start_s;
          return (
            <span
              key={`${word.start_s}-${index}`}
              style={{
                color: word.is_highlight ? CAPTION_HIGHLIGHT : CAPTION_WHITE,
                // Black outline for sound-off legibility on any still.
                WebkitTextStroke: '2px #000000',
                paintOrder: 'stroke fill',
                textShadow: '0 2px 10px rgba(0,0,0,0.85)',
                // Subtle pop on the word currently being spoken.
                opacity: isActive ? 1 : 0.92,
                transform: isActive ? 'translateY(-2px)' : 'none',
              }}
            >
              {word.word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}
