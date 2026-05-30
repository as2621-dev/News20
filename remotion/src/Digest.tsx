import type { FC } from 'react';
import { AbsoluteFill, Audio, interpolate, Sequence, staticFile, useCurrentFrame } from 'remotion';
import { CaptionTrack } from './components/CaptionTrack';
import { HeadlineCard } from './components/HeadlineCard';
import { KenBurnsImage } from './components/KenBurnsImage';
import type { DigestManifest, KenBurns } from './manifest';

/** How long the headline intro card holds before fading out (≈2.5s @ 30fps). */
const HEADLINE_INTRO_FRAMES = 75;
/** Cross-fade length for the headline intro's exit. */
const HEADLINE_FADE_FRAMES = 15;

/**
 * Static-first default drift (poster-pipeline §10): a very subtle slow zoom, NO pan, so a long
 * held poster does not feel dead while the caption band stays perfectly still.
 */
const DEFAULT_KEN_BURNS: KenBurns = {
  startScale: 1.0,
  endScale: 1.04,
  startTranslateX: 0,
  endTranslateX: 0,
  startTranslateY: 0,
  endTranslateY: 0,
};

/**
 * Resolve a manifest asset reference to a URL Remotion can load.
 *
 * Fixture/relative paths (e.g. `fixtures/assets/cut-1.png`) are resolved from Remotion's
 * `public/` via `staticFile`. Absolute paths / `http(s)` URLs (what SP4's Python manifest will
 * emit) are passed through untouched.
 */
function resolveSrc(src: string): string {
  if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('/')) {
    return src;
  }
  return staticFile(src);
}

/** Brief headline intro overlay that fades out after the opening beat. */
const HeadlineIntro: FC<{ headlineText: string }> = ({ headlineText }) => {
  const frame = useCurrentFrame();
  // Hold fully visible, then cross-fade out over the final HEADLINE_FADE_FRAMES.
  const opacity = interpolate(
    frame,
    [HEADLINE_INTRO_FRAMES - HEADLINE_FADE_FRAMES, HEADLINE_INTRO_FRAMES],
    [1, 0],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' },
  );
  return (
    <AbsoluteFill style={{ opacity }}>
      <HeadlineCard headlineText={headlineText} />
    </AbsoluteFill>
  );
};

/**
 * The 9:16 single-poster digest composition. One poster-grade image fills the frame for the
 * whole timeline (with a static-first gentle drift), a brief headline card intros the story
 * over the opening beat then fades, and a word-by-word caption track + narration audio run
 * over the entire timeline. Per poster-pipeline §2/§10: the image is the background, text is a
 * separate overlay layer, and motion never sits under the still text band.
 */
export const Digest: FC<DigestManifest> = (manifest) => {
  const { posterSrc, headlineText, captionTrack, audioSrc, durationInFrames, kenBurns } = manifest;

  return (
    <AbsoluteFill style={{ backgroundColor: '#020617' }}>
      {/* Single full-frame poster for the whole timeline. */}
      <KenBurnsImage
        imageSrc={resolveSrc(posterSrc)}
        durationInFrames={durationInFrames}
        kenBurns={kenBurns ?? DEFAULT_KEN_BURNS}
      />

      {/* Headline intro: visible for the opening beat, then fades out. */}
      <Sequence from={0} durationInFrames={HEADLINE_INTRO_FRAMES}>
        <HeadlineIntro headlineText={headlineText} />
      </Sequence>

      {/* Caption track + audio span the entire timeline. */}
      <CaptionTrack track={captionTrack} />
      <Audio src={resolveSrc(audioSrc)} />
    </AbsoluteFill>
  );
};
