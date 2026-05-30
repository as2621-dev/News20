import { Config } from '@remotion/cli/config';

// Reason: 9:16 stills + captions are PNG-friendly; keep H.264 default for MP4 output in SP4.
Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);

// Reason: Inter / JetBrains Mono are referenced by font-family with system fallbacks in the
// components; no webpack/font-fetch wiring is needed for the still/preview render. SP4 can add
// @remotion/google-fonts if exact webfonts become required for the final MP4s.
