import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Capacitor iOS shell config (Phase 4c SP1).
 *
 * Wraps the Next.js **static export** (`next.config.ts` `output: "export"`, emitted
 * to `out/`) in a native iOS WebView. `webDir` MUST match that export dir — `cap
 * sync` copies `out/` into the generated `ios/App/App/public` bundle, so the binary
 * ships the SPA offline-first and reaches Supabase/the Railway worker over the
 * network at runtime (port-map §6 / `reference/stack-notes.md`).
 *
 * `appId` is the App Store bundle identifier (SP0 creates the matching App ID); the
 * native `ios/` project bakes it into `PRODUCT_BUNDLE_IDENTIFIER`, so change it here
 * BEFORE generating `ios/` on the build Mac if it ever needs to differ.
 *
 * Note: `ios/` is generated on a Mac with full Xcode + CocoaPods (`npx cap add ios`);
 * this config is the machine-independent half scaffolded ahead of that.
 */
const config: CapacitorConfig = {
  appId: "com.blip.app",
  appName: "Blip",
  webDir: "out",
};

export default config;
