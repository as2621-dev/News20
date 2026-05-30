import type { ReactElement } from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

/** Props for the cut-1 headline overlay card. */
export interface HeadlineCardProps {
  /** Headline text shown during the hook (cut 1). */
  headlineText: string;
}

/**
 * The headline card overlaid on cut 1 (the 0–2s hook). A near-black scrim lifts the headline
 * off the still; the title rises in with a restrained spring. Inter 500 for the headline,
 * JetBrains Mono 600 for the small "NEWS20 / BRIEFING" metadata label (design-language tokens).
 *
 * Rendered only over cut 1 by `Digest`; it does not manage its own visibility window.
 */
export function HeadlineCard({ headlineText }: HeadlineCardProps): ReactElement {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entrance = spring({ frame, fps, config: { damping: 200 }, durationInFrames: 18 });
  const translateY = interpolate(entrance, [0, 1], [40, 0]);

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'flex-start', padding: 64 }}>
      {/* Scrim: darken the still so the headline reads against any image. */}
      <AbsoluteFill
        style={{
          background: 'linear-gradient(180deg, rgba(2,6,23,0.85) 0%, rgba(2,6,23,0.45) 55%, rgba(2,6,23,0.85) 100%)',
        }}
      />
      <div
        style={{
          position: 'relative',
          opacity: entrance,
          transform: `translateY(${translateY}px)`,
          maxWidth: 920,
        }}
      >
        <div
          style={{
            fontFamily: "'JetBrains Mono', ui-monospace, 'SF Mono', monospace",
            fontWeight: 600,
            fontSize: 24,
            lineHeight: 1.2,
            letterSpacing: 2,
            color: '#A1A1AA',
            marginBottom: 24,
          }}
        >
          NEWS20 / DAILY BRIEFING
        </div>
        <h1
          style={{
            fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
            fontWeight: 500,
            fontSize: 96,
            lineHeight: 1.04,
            color: '#FFFFFF',
            margin: 0,
            // Sharp/editorial: square left edge accent bar.
            borderLeft: '6px solid #FACC15',
            paddingLeft: 32,
          }}
        >
          {headlineText}
        </h1>
      </div>
    </AbsoluteFill>
  );
}
