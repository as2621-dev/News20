import type { ReactElement } from 'react';
import { Composition } from 'remotion';
import { Digest } from './Digest';
import sampleManifest from './fixtures/sample-manifest.json';
import type { DigestManifest } from './manifest';

const defaultManifest = sampleManifest as DigestManifest;

/**
 * Registers the `Digest` composition. Default props come from the self-contained fixture so
 * Remotion Studio / `remotion still` render with zero dependency on SP1/SP2/SP4 output.
 *
 * `calculateMetadata` derives the timeline length and fps from the INCOMING props (which
 * Remotion builds by merging `defaultProps` with any `--props` override). This is required so
 * each SP4 digest renders at its true audio length: `--props` overrides props but NOT the
 * statically-registered `durationInFrames`, so without this the composition would lock to the
 * fixture's duration and clip longer digests / leave a black-silent tail on shorter ones.
 * The static `durationInFrames`/`fps` below are only the fallback so Studio still opens.
 */
export function RemotionRoot(): ReactElement {
  return (
    <Composition
      id="Digest"
      component={Digest}
      durationInFrames={defaultManifest.durationInFrames}
      fps={defaultManifest.fps}
      width={defaultManifest.width}
      height={defaultManifest.height}
      defaultProps={defaultManifest}
      calculateMetadata={({ props }) => ({
        // Reason: the single poster holds for the full audio; SP4 sets durationInFrames to
        // round(audio_duration_s * fps).
        durationInFrames: props.durationInFrames,
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
  );
}
