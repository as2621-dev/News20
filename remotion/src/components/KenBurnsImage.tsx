import type { ReactElement } from 'react';
import { AbsoluteFill, Img, interpolate, useCurrentFrame } from 'remotion';
import type { KenBurns } from '../manifest';

/** Props for the full-frame poster still. */
export interface KenBurnsImageProps {
  /** Image source (resolved by the caller via `staticFile()` or an absolute path). */
  imageSrc: string;
  /** How many frames the still is held — drives the interpolation range. */
  durationInFrames: number;
  /** Start/end scale + pan offsets for the slow zoom-and-pan. */
  kenBurns: KenBurns;
}

/**
 * A full-bleed 9:16 poster still with a slow Ken Burns zoom/pan. Scale and translate
 * interpolate linearly from the `kenBurns` start values to its end values across the whole
 * `durationInFrames`. Static-first: the caller passes a near-imperceptible drift by default.
 *
 * @example
 * <KenBurnsImage imageSrc={staticFile('poster.png')} durationInFrames={1518} kenBurns={kb} />
 */
export function KenBurnsImage({ imageSrc, durationInFrames, kenBurns }: KenBurnsImageProps): ReactElement {
  const frame = useCurrentFrame();
  const progressRange: [number, number] = [0, Math.max(1, durationInFrames - 1)];

  const scale = interpolate(frame, progressRange, [kenBurns.startScale, kenBurns.endScale], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const translateX = interpolate(frame, progressRange, [kenBurns.startTranslateX, kenBurns.endTranslateX], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const translateY = interpolate(frame, progressRange, [kenBurns.startTranslateY, kenBurns.endTranslateY], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{ backgroundColor: '#020617', overflow: 'hidden' }}>
      <Img
        src={imageSrc}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          transform: `scale(${scale}) translate(${translateX}px, ${translateY}px)`,
        }}
      />
    </AbsoluteFill>
  );
}
